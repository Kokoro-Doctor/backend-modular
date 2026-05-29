```
flowchart LR

%% =========================
%% PATIENT SIDE
%% =========================
subgraph PAT["👤 Patient Side"]
P[Patient]
ABHA[ABHA App / PHR App]
end

%% =========================
%% HOSPITALS
%% =========================
subgraph HOSP["🏥 Hospitals"]
H1[Hospital A]
H2[Hospital B]
end

%% =========================
%% KOKORO PLATFORM
%% =========================
subgraph KOK["💻 Kokoro Platform"]
PORTAL[Hospital Portal]
API[Kokoro Backend APIs]
DB[(Central Medical DB)]
CONSENT[Consent Manager]
FHIR[FHIR Converter]
ENC[Encryption Service]
end

%% =========================
%% ABDM
%% =========================
subgraph ABDM["🌐 ABDM Network"]
HFR[HFR Registry]
GW[ABDM Gateway]
CM[Consent System]
end

%% =========================
%% HFR FLOW
%% =========================
H1 -->|Register Facility| HFR
H2 -->|Register Facility| HFR

%% =========================
%% HOSPITAL USING KOKORO
%% =========================
H1 --> PORTAL
H2 --> PORTAL

PORTAL --> API
API --> DB

%% =========================
%% CONSENT FLOW
%% =========================
P --> ABHA

API -->|1. Consent Request| GW
GW --> CM
CM -->|2. Ask Permission| ABHA

ABHA -->|3. Approve Consent| CM
CM --> GW
GW -->|4. Consent Artefact| CONSENT

%% =========================
%% DATA REQUEST FLOW
%% =========================
API -->|5. Request Records| GW
GW -->|6. Notify HIP| API

%% =========================
%% DATA PREPARATION
%% =========================
API --> DB
DB --> FHIR
FHIR --> ENC

%% =========================
%% DATA SHARING
%% =========================
ENC -->|7. Encrypted FHIR Data| GW
GW -->|8. Transfer Records| API

%% =========================
%% FINAL DISPLAY
%% =========================
API -->|9. Decrypt + Show Records| PORTAL
PORTAL --> P

%% =========================
%% COLORS
%% =========================
classDef patient fill:#FFE5B4,stroke:#333,stroke-width:2px,color:#000;
classDef hospital fill:#FFD6D6,stroke:#333,stroke-width:2px,color:#000;
classDef kokoro fill:#D6E4FF,stroke:#333,stroke-width:2px,color:#000;
classDef abdm fill:#D8FFD8,stroke:#333,stroke-width:2px,color:#000;

class P,ABHA patient;
class H1,H2 hospital;
class PORTAL,API,DB,CONSENT,FHIR,ENC kokoro;
class HFR,GW,CM abdm;
```
