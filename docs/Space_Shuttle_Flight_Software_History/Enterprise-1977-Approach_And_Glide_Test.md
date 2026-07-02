# The 1977 Approach and Landing Tests (ALT): PASS Foundations

The Approach and Landing Tests (ALT) conducted in 1977 aboard the prototype orbiter *Enterprise* represented the crucible for the Primary Avionics Software System (PASS). Before these trials, digital fly-by-wire (DFBW) technology for unpowered, heavy atmospheric vehicles was largely theoretical.



## 1. Proving Digital Fly-By-Wire (DFBW)
The ALT flights proved that the PASS flight software could serve as the primary link between the pilot and the aerodynamic control surfaces.

* **Critical Reliability:** Unlike modern aircraft with mechanical backups, *Enterprise* relied entirely on the software to move the elevons and rudder. If the software had failed, the pilot would have lost control. 
* **Stability Augmentation:** Because the shuttle possessed notoriously poor natural flight characteristics (the "flying brick" profile), the PASS implemented sophisticated stability augmentation, allowing human pilots to manage the high-speed approach and touchdown safely.

## 2. Technical Challenges: Gain Scheduling
One of the most significant engineering hurdles during ALT was managing control gains across different flight regimes. The software had to transition smoothly from high-altitude/high-speed flight to low-altitude/low-speed landing configurations.



* **Hard-Coded Look-up Tables:** Rather than modern adaptive control, the HAL/S code utilized static, hard-coded look-up tables to determine control gains.
* **Actuator Latency:** The ALT tests forced developers to account for the physical lag of hydraulic actuators. This necessitated the "Rate Limiting" logic within HAL/S to prevent the software from over-correcting, a feature that remained critical for all subsequent orbital missions.

## 3. System Architecture & The "Cyclic Polling" Model
The success of the ALT software validated the "cyclic polling" architecture, which became the standard for all shuttle missions:

* **Fixed Timing Loops:** By running Guidance, Navigation, and Control (GN&C) tasks at fixed intervals (25Hz, 50Hz, 100Hz), engineers eliminated the timing jitter that would have rendered manual landing impossible.
* **Modularity:** ALT software was significantly smaller and less complex than the final mission software, providing a clear, "clean" implementation of the core HAL/S execution engine. 

The ALT trials proved that deterministic software, running on fixed-cycle hardware, was the only reliable way to command a vehicle as complex as the Space Shuttle.
