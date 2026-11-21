"""
CardioKey ECG identity + wellness processing.
Uses the frozen ecg_pretrained encoder; never trains or modifies the model.
"""
import os, json, time
from pathlib import Path
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow.keras.models import load_model
from scipy.signal import butter, sosfiltfilt, find_peaks, welch

BASE_DIR = Path(os.getenv("CARDIOKEY_BASE_DIR", "/opt/cardiokey"))
MODEL_DIR = Path(os.getenv("CARDIOKEY_MODEL_DIR", str(BASE_DIR / "models/CardioKey_Final_Model_Package")))
DATA_DIR = Path(os.getenv("CARDIOKEY_DATA_DIR", str(BASE_DIR / "data")))
HEALTH_DIR = DATA_DIR / "health_baselines"
GALLERY_PATH = DATA_DIR / "enrollment_gallery_additions.npz"
HEALTH_DIR.mkdir(parents=True, exist_ok=True)
MIN_ENROLLMENT_WINDOWS = int(os.getenv("MIN_ENROLLMENT_WINDOWS", "5"))
THRESHOLD = 0.366  # development-only threshold; do not present as independently validated

def _load_json(path):
    with open(path, "r") as f: return json.load(f)

def load_assets():
    status = _load_json(MODEL_DIR / "FINAL_MODEL_STATUS.json")
    config = _load_json(MODEL_DIR / "cardiokey_model_config.json")
    threshold_info = _load_json(MODEL_DIR / "authentication_threshold.json")
    if status.get("selected_candidate") != "ecg_pretrained":
        raise RuntimeError("Unexpected selected candidate in model package.")
    if config.get("input_samples") != 256 or config.get("target_sampling_rate_hz") != 100:
        raise RuntimeError("Model preprocessing config mismatch.")
    if int(config.get("embedding_dimension", 0)) != 64:
        raise RuntimeError("Expected 64-dimensional embedding.")
    threshold = float(threshold_info["threshold"])
    model = load_model(MODEL_DIR / "cardiokey_final_encoder.keras", compile=False)
    if len(model.inputs) != 1 or tuple(model.inputs[0].shape[1:]) != (256, 1):
        raise RuntimeError("Encoder input shape mismatch.")
    if int(model.output_shape[-1]) != 64:
        raise RuntimeError("Encoder output shape mismatch.")
    with np.load(MODEL_DIR / "enrollment_templates.npz", allow_pickle=False) as z:
        people = [str(x).lower() for x in z["people"].tolist()]
        templates = np.asarray(z["templates"], np.float32)
    if templates.shape != (len(people), 64) or not np.isfinite(templates).all():
        raise RuntimeError("Packaged enrollment templates are invalid.")
    norms = np.linalg.norm(templates, axis=1)
    if np.any(norms <= 0): raise RuntimeError("Zero-norm packaged template.")
    templates = templates / norms[:, None]
    return model, people, templates, threshold, status, config

ENCODER, PEOPLE, TEMPLATES, MODEL_THRESHOLD, MODEL_STATUS, MODEL_CONFIG = load_assets()

def _time_scale(df):
    if "timestamp_us" in df.columns: return "timestamp_us", 1e6
    if "timestamp_ms" in df.columns: return "timestamp_ms", 1e3
    raise ValueError("CSV requires timestamp_us or timestamp_ms.")

def _ecg_arrays(df):
    tc, scale = _time_scale(df)
    if "sensor" in df.columns:
        d = df[df["sensor"].astype(str).str.strip().str.upper().eq("ECG")].copy()
    else:
        d = df.copy()
    if "ecg_raw" not in d.columns: raise ValueError("CSV is missing ecg_raw.")
    t = pd.to_numeric(d[tc], errors="coerce").to_numpy(float) / scale
    x = pd.to_numeric(d["ecg_raw"], errors="coerce").to_numpy(float)
    ok = np.isfinite(t) & np.isfinite(x)
    t, x = t[ok], x[ok]
    if len(t) < 20: raise ValueError("Too few valid ECG samples.")
    order = np.argsort(t); t, x = t[order], x[order]
    q = pd.DataFrame({"t":t,"x":x}).groupby("t",as_index=False).x.median()
    return q.t.to_numpy(), q.x.to_numpy(), d

def resample_uniform(t, x, fs=100.0):
    if len(t) < 10 or t[-1] <= t[0]: raise ValueError("Insufficient/invalid timestamps.")
    duration = float(t[-1]-t[0])
    fs_est = (len(t)-1)/duration
    grid = np.arange(t[0], t[-1], 1.0/fs)
    if len(grid) < 256: raise ValueError("Fewer than 256 resampled ECG points.")
    return np.interp(grid,t,x).astype(np.float32), fs_est

def robust_model_input(segment):
    x=np.asarray(segment,np.float32).reshape(-1)
    if x.size != 256: raise ValueError("Encoder requires exactly 256 samples.")
    med=float(np.median(x)); mad=float(np.median(np.abs(x-med))); scale=1.4826*mad
    if not np.isfinite(scale) or scale < 1e-8: raise ValueError("ECG segment has near-zero MAD.")
    return np.clip((x-med)/scale,-8,8).reshape(1,256,1).astype(np.float32)

def _embedding(segment):
    e=np.asarray(ENCODER.predict(robust_model_input(segment),verbose=0),np.float32).reshape(-1)
    if e.shape != (64,) or not np.isfinite(e).all(): raise ValueError("Invalid encoder embedding.")
    n=float(np.linalg.norm(e))
    if n <= 0: raise ValueError("Zero-norm encoder embedding.")
    return e/n

def _lead_off_fraction(df, t0, t1):
    if "lead_off" not in df.columns: return None
    tc, scale = _time_scale(df)
    d=df[df["sensor"].astype(str).str.upper().eq("ECG")].copy() if "sensor" in df.columns else df.copy()
    tt=pd.to_numeric(d[tc],errors="coerce").to_numpy(float)/scale
    vals=d["lead_off"]
    if vals.dtype == object:
        vals=vals.astype(str).str.lower().map({"true":1,"false":0,"yes":1,"no":0,"on":1,"off":0}).fillna(pd.to_numeric(vals,errors="coerce"))
    v=pd.to_numeric(vals,errors="coerce").to_numpy(float)
    m=np.isfinite(tt)&np.isfinite(v)&(tt>=t0)&(tt<t1)
    return float(np.mean(v[m]!=0)) if np.any(m) else None

def make_enrollment_segments(df):
    t,x,raw=_ecg_arrays(df)
    duration=float(t[-1]-t[0])
    if duration < 5: raise ValueError("Enrollment recording shorter than 5 seconds.")
    segs=[]; audits=[]
    for i in range(int(duration//5)):
        a=float(t[0]+5*i); b=a+5
        m=(t>=a)&(t<b); tw=t[m]; xw=x[m]; reasons=[]
        if len(tw)<20: reasons.append("too_few_samples")
        else:
            if tw[-1]-tw[0] < 4.5: reasons.append("coverage_below_4.5s")
            gap=float(np.max(np.diff(tw))*1000)
            if gap>100: reasons.append("timestamp_gap_over_100ms")
            if np.nanmin(xw)>=0 and np.nanmax(xw)<=4095:
                rail=float(np.mean((xw<=1)|(xw>=4094)))
                if rail>0.01: reasons.append("adc_rail_fraction_over_1pct")
            else: rail=None
            lead=_lead_off_fraction(df,a,b)
            if lead is not None and lead>0.01: reasons.append("lead_off_fraction_over_1pct")
            if not reasons:
                y,_=resample_uniform(tw,xw,100)
                if len(y)<256: reasons.append("fewer_than_256_resampled_samples")
                else:
                    st=(len(y)-256)//2; segs.append(y[st:st+256])
        audits.append({"window_index":i,"start_seconds":a,"end_seconds":b,"samples":len(tw),
                       "accepted":not bool(reasons),"rejection_reasons":";".join(reasons)})
    return segs,pd.DataFrame(audits),duration

def _channel_arrays(df, channel, col):
    tc,scale=_time_scale(df)
    if "sensor" in df.columns:
        d=df[df["sensor"].astype(str).str.strip().str.upper().eq(channel)].copy()
    else:
        d=df.copy()
    if col not in d.columns: return np.array([]),np.array([])
    t=pd.to_numeric(d[tc],errors="coerce").to_numpy(float)/scale
    x=pd.to_numeric(d[col],errors="coerce").to_numpy(float)
    ok=np.isfinite(t)&np.isfinite(x)
    # ESP32 wide CSV writes zero when no new MAX30102 FIFO sample was read.
    # Exclude those placeholders for PPG; actual raw MAX30102 samples are positive.
    if channel=="PPG" and "sensor" not in df.columns:
        ok &= (x > 0)
    t,x=t[ok],x[ok]
    if len(t)<2: return np.array([]),np.array([])
    order=np.argsort(t); t,x=t[order],x[order]
    q=pd.DataFrame({"t":t,"x":x}).groupby("t",as_index=False).x.median()
    return q.t.to_numpy(),q.x.to_numpy()

def _uniform_channel(df, channel, col, fs, minimum_seconds=10):
    t,x=_channel_arrays(df,channel,col)
    if len(t)<20 or t[-1]<=t[0]: return np.array([]),float("nan"),0.0
    dur=float(t[-1]-t[0]); est=(len(t)-1)/dur
    grid=np.arange(t[0],t[-1],1/fs)
    if len(grid)<int(fs*minimum_seconds): return np.array([]),est,dur
    return np.interp(grid,t,x),est,dur

def _ecg_health(df):
    tr,xr=_channel_arrays(df,"ECG","ecg_raw")
    gap=float(np.max(np.diff(tr))*1000) if len(tr)>1 else float("inf")
    x,fs,dur=_uniform_channel(df,"ECG","ecg_raw",100)
    if gap>100: return {"status":"ECG_QUALITY_REJECTED_FOR_HEALTH","max_gap_ms":round(gap,1)}
    if len(x)<1000: return {"status":"INSUFFICIENT_ECG_DURATION","duration_seconds":round(dur,2)}
    xf=sosfiltfilt(butter(3,[5,20],btype="bandpass",fs=100,output="sos"),x-np.median(x))
    peaks,_=find_peaks(xf,distance=30,prominence=max(float(np.std(xf))*.45,1e-6))
    rr=np.diff(peaks)/100
    rr=rr[(rr>=60/220)&(rr<=60/35)]
    if len(rr)<4: return {"status":"INSUFFICIENT_OR_NOISY_ECG","detected_r_peaks":int(len(peaks))}
    bpm=60/rr
    out={"status":"ESTIMATED","ecg_mean_heart_rate_bpm":round(float(np.mean(bpm)),1),
         "ecg_median_heart_rate_bpm":round(float(np.median(bpm)),1),"valid_rr_intervals":int(len(rr)),
         "duration_seconds":round(dur,2),"hrv_status":"INSUFFICIENT_DURATION_FOR_STANDARD_5_MIN_HRV"}
    if dur>=60 and len(rr)>=30:
        diff=np.diff(rr)*1000
        out["rmssd_ms"]=round(float(np.sqrt(np.mean(diff**2))),1)
        out["sdnn_ms"]=round(float(np.std(rr,ddof=1)*1000),1)
        out["hrv_status"]="SHORT_RECORDING_ESTIMATE; 5_MINUTE_RESTING_RECORDING_PREFERRED"
    if len(rr)>=20:
        cv=float(np.std(rr)/np.mean(rr))
        out["rr_interval_cv"]=round(cv,3)
        out["irregular_rhythm_screen"]="IRREGULARITY_FLAG_REQUIRES_CLINICAL_ECG_REVIEW" if cv>.20 else "NO_SIMPLE_IRREGULARITY_FLAG"
    else: out["irregular_rhythm_screen"]="INSUFFICIENT_BEATS"
    out["afib_status"]="NOT_DIAGNOSED; RR irregularity alone cannot identify or exclude AF"
    return out

def _ppg_health(df):
    tr,xr=_channel_arrays(df,"PPG","ir_raw")
    gap=float(np.max(np.diff(tr))*1000) if len(tr)>1 else float("inf")
    x,fs,dur=_uniform_channel(df,"PPG","ir_raw",50)
    if gap>150: return {"status":"PPG_QUALITY_REJECTED","max_gap_ms":round(gap,1)}
    if len(x)<500: return {"status":"PPG_UNAVAILABLE_OR_TOO_SHORT","duration_seconds":round(dur,2)}
    xf=sosfiltfilt(butter(2,[.5,4],btype="bandpass",fs=50,output="sos"),x-np.median(x))
    peaks,_=find_peaks(xf,distance=10,prominence=max(float(np.std(xf))*.25,1e-8))
    ibi=np.diff(peaks)/50; ibi=ibi[(ibi>=60/220)&(ibi<=60/35)]
    out={"status":"ESTIMATED","duration_seconds":round(dur,2),"ppg_sample_rate_hz":round(float(fs),1),
         "ppg_pulse_rate_bpm":round(float(60/np.median(ibi)),1) if len(ibi)>=3 else None}
    if dur>=60:
        f,p=welch(xf,fs=50,nperseg=min(len(xf),3000)); band=(f>=.10)&(f<=.40)
        out["respiratory_rate_bpm"]=round(float(f[band][np.argmax(p[band])]*60),1) if np.any(band) else None
        out["respiratory_rate_status"]="EXPLORATORY_PPG_MODULATION_ESTIMATE"
    else:
        out["respiratory_rate_bpm"]=None
        out["respiratory_rate_status"]="NEEDS_AT_LEAST_60_SECONDS"
    return out

def build_health_report(df, age_years=None, sex=None, accepted_windows=None, baseline=None):
    ecg=_ecg_health(df); ppg=_ppg_health(df)
    report={"report_type":"WELLNESS_SIGNAL_ESTIMATES_NOT_MEDICAL_DIAGNOSIS",
            "demographics":{"age_years":age_years,"sex_recorded":sex},
            "ecg":ecg,"ppg":ppg,
            "quality_gate":{"accepted_ecg_enrollment_windows":accepted_windows,
                            "ecg_health_metrics_available":ecg.get("status")=="ESTIMATED",
                            "ppg_health_metrics_available":ppg.get("status")=="ESTIMATED"},
            "interpretation":"Signal-derived wellness estimates only; age/sex are metadata, not validated clinical cutoffs."}
    if baseline:
        bh=baseline.get("ecg",{}).get("ecg_median_heart_rate_bpm")
        nh=ecg.get("ecg_median_heart_rate_bpm")
        if bh is not None and nh is not None:
            report["change_from_personal_baseline"]={"median_ecg_hr_change_bpm":round(float(nh-bh),1),
                "note":"Descriptive within-person change; not a diagnosis."}
    return report

def _baseline_path(person): return HEALTH_DIR / (str(person).lower()+".json")
def load_baseline(person):
    p=_baseline_path(person)
    return json.loads(p.read_text()) if p.exists() else None

def _save_gallery(additions):
    people=sorted(additions)
    vectors=np.vstack([additions[p] for p in people]).astype(np.float32) if people else np.empty((0,64),np.float32)
    tmp=GALLERY_PATH.with_suffix(".tmp.npz")
    np.savez_compressed(tmp,people=np.asarray(people,dtype=str),templates=vectors)
    os.replace(tmp,GALLERY_PATH)

def get_gallery():
    people=list(PEOPLE); vectors=[v.copy() for v in TEMPLATES]
    if GALLERY_PATH.exists():
        with np.load(GALLERY_PATH,allow_pickle=False) as z:
            ep=[str(x).lower() for x in z["people"].tolist()]
            ev=np.asarray(z["templates"],np.float32)
        if ev.shape!=(len(ep),64) or not np.isfinite(ev).all(): raise RuntimeError("Additional gallery invalid.")
        if set(ep).intersection(people): raise RuntimeError("Duplicate identities in additional gallery.")
        for p,v in zip(ep,ev):
            n=np.linalg.norm(v)
            if n<=0: raise RuntimeError("Zero-norm added template.")
            people.append(p); vectors.append(v/n)
    return people,np.vstack(vectors)

def enroll_person(df, person_id, age_years=None, sex=None):
    person=str(person_id).strip().lower()
    if not person: raise ValueError("Missing participant identifier.")
    people,_=get_gallery()
    if person in people: raise ValueError("Participant already has a template; duplicate enrollment blocked.")
    segments,audit,duration=make_enrollment_segments(df)
    accepted=int(audit["accepted"].sum())
    if accepted<MIN_ENROLLMENT_WINDOWS:
        raise ValueError(f"Enrollment rejected: {accepted} accepted 5-second windows; minimum {MIN_ENROLLMENT_WINDOWS}.")
    embs=np.vstack([_embedding(s) for s in segments])
    mean=embs.mean(axis=0); norm=float(np.linalg.norm(mean))
    if norm<=0 or not np.isfinite(norm): raise ValueError("Invalid aggregate enrollment template.")
    template=(mean/norm).astype(np.float32)
    # The same recording is used for baseline, but metric availability is independently gated.
    health=build_health_report(df,age_years,sex,accepted_windows=len(embs))
    additions={}
    if GALLERY_PATH.exists():
        with np.load(GALLERY_PATH,allow_pickle=False) as z:
            for p,v in zip(z["people"].tolist(),z["templates"]): additions[str(p).lower()]=np.asarray(v,np.float32)
    additions[person]=template
    _save_gallery(additions)
    health["baseline_source"]="same QC-accepted enrollment recording; each metric has its own duration/quality gate"
    _baseline_path(person).write_text(json.dumps(health,indent=2,allow_nan=False))
    return {"status":"ENROLLMENT_TEMPLATE_CREATED","participant":person,"accepted_windows":len(embs),
            "total_windows":len(audit),"duration_seconds":duration,"embedding_dimension":64,
            "health_baseline":health,"qc_audit":audit.to_dict(orient="records")}

def authenticate(df, age_years=None, sex=None):
    t,x,_=_ecg_arrays(df); y,fs=resample_uniform(t,x,100)
    segs=[y[i:i+256] for i in range(0,len(y)-256+1,128)]
    if not segs: raise ValueError("No complete 2.56-second ECG segments.")
    E=np.vstack([_embedding(s) for s in segs])
    people,gallery=get_gallery()
    scores=E @ gallery.T
    best=np.argmax(scores,axis=1)
    vote=np.bincount(best,minlength=len(people))
    winner=people[int(np.argmax(vote))]
    score=float(np.median(scores[:,people.index(winner)]))
    # This is a development-derived provisional rule, not calibrated for expanded gallery.
    decision="PROVISIONAL_MATCH" if score>=MODEL_THRESHOLD else "NO_MATCH"
    baseline=load_baseline(winner) if decision=="PROVISIONAL_MATCH" else None
    health=build_health_report(df,age_years,sex,baseline=baseline)
    return {"identity_model":"ecg_pretrained","identity_modality":"ECG",
            "status":decision,"user":winner if decision=="PROVISIONAL_MATCH" else "Unknown",
            "candidate":winner,"aggregate_cosine_similarity":score,
            "development_threshold":MODEL_THRESHOLD,
            "threshold_status":"development_only; expanded gallery requires recalibration",
            "window_count":len(segs),"ecg_estimated_sampling_rate_hz":float(fs),
            "recording_duration_seconds":float(t[-1]-t[0]),
            "health_status":health,"ppg_support":_ppg_health(df)}
