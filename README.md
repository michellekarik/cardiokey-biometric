# CardioKey
### A Dual-Purpose ECG–PPG Fusion System for Non-Invasive Biometric Authentication and Continuous Wellness Monitoring

CardioKey is a physiological biometric authentication system that explores the use of Electrocardiography (ECG) and Photoplethysmography (PPG) signals for identity verification and continuous wellness monitoring.

The system integrates physiological signal acquisition through an ESP32-based hardware module, machine-learning-based signal processing, a Flask backend deployed on AWS EC2, and an Android application.

By investigating ECG-only, PPG-only, and multimodal ECG–PPG fusion approaches, CardioKey explores the potential of physiological signals as an additional biometric authentication mechanism.

---

## 1. System Overview

CardioKey consists of four primary components:

| Component | Description |
|---|---|
| ESP32 Firmware | Acquires ECG and PPG signals through connected physiological sensors. |
| Flask Backend | Handles application requests, communication, and coordination of backend processing. |
| ML Processing Workers | Perform the signal-processing and biometric operations deployed on AWS EC2. |
| Android Application | Provides the user-facing interface for interacting with the CardioKey system. |

### System Architecture

The general system workflow is:

1. ECG and PPG signals are acquired using the connected sensors.
2. The ESP32 collects and prepares physiological signal data.
3. Acquired data is transmitted to the backend.
4. The Flask backend handles incoming requests and coordinates processing.
5. The machine-learning workers process the physiological signals.
6. Authentication-related results are returned through the backend.
7. The Android application provides the corresponding user interface and system interaction.

The exact processing sequence depends on the implemented workflow and configuration.

---

## 2. Hardware Components

CardioKey uses the following hardware components:

| Component | Purpose |
|---|---|
| ESP32 | Microcontroller responsible for sensor interfacing and data acquisition. |
| AD8232 ECG Sensor | Acquires electrocardiographic signals. |
| MAX30102 Sensor | Acquires photoplethysmographic signals. |

### Hardware Connections

The ESP32 interfaces with:

- AD8232 ECG sensor.
- MAX30102 PPG sensor.

The firmware contains the actual sensor pin configuration and acquisition logic.

Refer to the Arduino source file for the complete hardware configuration and GPIO assignments.

---

## 3. Software Architecture

### A. ESP32 Firmware

**Source:** Arduino `.ino` file

The ESP32 firmware is responsible for interacting with the physiological sensors and acquiring ECG and PPG data.

Its responsibilities include:

- Sensor initialization.
- ECG signal acquisition.
- PPG signal acquisition.
- Signal-data preparation.
- Communication with the backend.
- Handling the configured acquisition workflow.

**Technologies:**
- ESP32.
- Arduino IDE.
- AD8232.
- MAX30102.
- Relevant Arduino libraries.

Refer to the firmware source for the exact implementation and library dependencies.

### B. Flask Backend

**Source:** `app.py`

The Flask backend acts as the application-side server responsible for handling incoming requests and coordinating communication with the processing components.

Its responsibilities include the API operations and application workflows implemented in the source code.

The backend connects the application and the machine-learning processing infrastructure.

**Technologies:**
- Python.
- Flask.
- REST API architecture.
- AWS EC2 integration.

Refer to `app.py` for the implemented routes, request formats, response structures, and configuration requirements.

### C. Machine-Learning Processing Workers

**Location:** AWS EC2 deployment

The worker files contain the machine-learning and signal-processing components used by CardioKey.

These components are responsible for the processing operations implemented in the project, including the relevant ECG, PPG, and multimodal biometric workflows.

The processing pipeline may include:

- Physiological signal preprocessing.
- Signal segmentation.
- Feature extraction.
- Modality-specific processing.
- Similarity or score computation.
- Authentication-related evaluation.
- ECG–PPG fusion.

The exact algorithms and processing stages are defined by the supplied worker source files.

**Deployment environment:**
- AWS EC2.
- Ubuntu Linux.
- Python-based processing environment.

Refer to the worker files and their dependency requirements for detailed setup instructions.

### D. Android Application

The Android application serves as the user-facing component of CardioKey.

It provides the application interface for interacting with the system and accessing the implemented authentication and wellness-monitoring functionality.

Refer to the Android project source code for:

- Application structure.
- Screens and user workflows.
- Backend communication.
- Authentication interaction.
- Configuration.
- Build requirements.

---

## 4. Biometric Processing

CardioKey investigates three physiological biometric configurations:

| Configuration | Description |
|---|---|
| ECG-only | Biometric processing using ECG signals. |
| PPG-only | Biometric processing using PPG signals. |
| ECG–PPG Fusion | Combined processing using ECG and PPG information. |

The multimodal approach explores the potential benefits of combining two physiological signal sources.

The actual preprocessing, feature extraction, model architecture, and fusion strategy are implemented in the machine-learning worker files.

---

## 5. Repository Structure

The source-code repository is organized around the following major components.

```text
CardioKey/
│
├── Android-App/
│   └── Android application source files
│
├── Backend/
│   └── app.py
│
├── ML-Workers/
│   └── Machine-learning and signal-processing worker files
│
├── ESP32-Firmware/
│   └── CardioKey.ino
│
└── README.md
```

*Note: The directory names above illustrate the recommended organization. Update them to match the actual uploaded repository structure.*

---

## 6. Setup and Configuration

Each component has its own development and execution environment.

### ESP32 Firmware

1. Install the Arduino IDE.
2. Install the required ESP32 board support package.
3. Install the libraries required by the firmware.
4. Connect the AD8232 and MAX30102 sensors according to the pin configuration in the source code.
5. Open the Arduino `.ino` file.
6. Select the appropriate ESP32 board and serial port.
7. Configure the required backend connection details.
8. Compile and upload the firmware.

### Flask Backend

1. Install Python.
2. Create and activate a Python virtual environment.
3. Install the dependencies listed in the backend requirements.
4. Configure the necessary environment variables.
5. Start the Flask application using the documented entry point.

Example:

```bash
python -m venv venv
```

Activate the environment and install dependencies:

```bash
pip install -r requirements.txt
```

Run the application:

```bash
python app.py
```

*The exact commands and dependency requirements should be verified against the uploaded backend files.*

### ML Processing Workers

1. Set up the required Python environment on the deployment machine.
2. Install the worker dependencies.
3. Configure the required environment variables and model paths.
4. Place the required model and supporting files in their expected locations.
5. Start the worker services according to the deployment configuration.

The deployed environment uses AWS EC2.

Refer to the actual deployment configuration and worker source files for the precise execution commands.

### Android Application

1. Install Android Studio.
2. Open the Android project.
3. Allow Gradle to synchronize dependencies.
4. Configure the backend endpoint.
5. Verify the required Android SDK configuration.
6. Build and run the application on an emulator or compatible Android device.

The exact SDK versions, dependencies, and build configuration should be taken from the Android project files.

---

## 7. Configuration and Environment Variables

The application may require configuration for:

- Backend API endpoint.
- AWS-related settings.
- Model and worker paths.
- Database connectivity.
- Authentication-related configuration.
- Sensor communication.

Use environment variables or appropriate configuration files to manage sensitive values.

**Never commit the following to a public repository:**

- AWS access keys.
- AWS secret keys.
- Private SSH keys.
- Database passwords.
- API tokens.
- Authentication secrets.
- Private certificates.
- User credentials.

Use a `.env.example` file containing placeholder values instead of actual credentials.

---

## 8. Deployment

The CardioKey backend and machine-learning processing components are deployed using AWS EC2.

The deployment environment provides the infrastructure required to host the backend and execute the processing workflows.

Deployment configuration should document:

- EC2 operating system.
- Required runtime dependencies.
- Backend startup procedure.
- Worker startup procedure.
- Network configuration.
- Required ports.
- Domain and HTTPS configuration, if applicable.
- Environment variables.
- Service management and restart procedure.

Actual deployment commands and infrastructure details should be maintained alongside the corresponding deployment files.

---

## 9. Technologies Used

The project incorporates the following technologies, subject to the implementations documented in the source code.

| Category | Technologies |
|---|---|
| Hardware | ESP32, AD8232, MAX30102 |
| Firmware | Arduino IDE, C/C++ |
| Backend | Python, Flask |
| Biometric Processing | Python, Machine Learning |
| Cloud Infrastructure | AWS EC2 |
| Mobile Application | Android |
| Communication | HTTP/REST APIs, as implemented |

---

## 10. Research and Evaluation

CardioKey investigates the performance of ECG-only, PPG-only, and ECG–PPG fusion configurations.

The research evaluates the system's biometric authentication capabilities using the experimental methodology and results documented in the accompanying project report.

The system is an academic research prototype.

Its performance should be interpreted within the scope of the documented experimental evaluation. It should not be considered a clinically validated diagnostic system or a production-ready high-security authentication solution.

---

## 11. Limitations

CardioKey is a research prototype, and its performance depends on factors such as:

- Physiological signal quality.
- Sensor placement.
- Motion artefacts.
- Individual physiological variation.
- Recording-session differences.
- Signal preprocessing.
- Biometric threshold selection.
- Cross-session recognition performance.

Further evaluation using larger and more diverse datasets is necessary to establish broader reliability and generalizability.

---

## 12. Future Work

Potential future improvements include:

- Improving signal-quality assessment.
- Enhancing cross-session biometric recognition.
- Optimizing ECG–PPG fusion.
- Expanding the participant dataset.
- Improving authentication reliability.
- Strengthening data privacy and security.
- Extending wellness-monitoring capabilities.
- Conducting further usability and validation studies.

---

## 13. Disclaimer

CardioKey is an academic research and development project.

The system explores physiological biometric authentication and wellness-monitoring applications. It is not intended to replace medical professionals, clinical diagnostic equipment, or established authentication mechanisms.

No medical diagnosis or clinical decision should be made solely using this research prototype.

---

## 14. Authors and Acknowledgements

Refer to the accompanying project report for the complete list of student contributors, project guide, and institutional affiliation.

---

**CardioKey – Exploring Physiological Signals for Biometric Authentication and Wellness Monitoring.**