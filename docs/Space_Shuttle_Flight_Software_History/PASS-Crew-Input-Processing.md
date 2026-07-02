# Processing Crew Inputs on the Space Shuttle PASS

Processing crew inputs on the Space Shuttle was a masterclass in deterministic, high-reliability embedded systems design. Because the Primary Avionics Software System (PASS) was responsible for keeping a 100-ton glider from tearing itself apart in the atmosphere, it could not rely on modern, unpredictable event-driven UI paradigms.

Instead, crew inputs were handled through a strictly scheduled, polled architecture governed by the HAL/S System Executive. Here is exactly how a keystroke or joystick movement made its way into the flight software.

## 1. The Hardware Interface (MDKs and CRTs)
The crew interacted with the PASS primarily through the **Multifunction CRT Display System (MCDS)**, which consisted of:

* **MDK (Multifunction Display Keyboard):** A specialized 32-key keypad.
* **CRTs:** The green-screen monitors displaying the current software operational sequence.
* **Hand Controllers:** Rotational Hand Controllers (RHC) and Translational Hand Controllers (THC) for manual flight.
* **Discrete Switches:** Hundreds of physical toggle switches on the flight deck.

## 2. The Data Bus and MDMs (Hardware to Software)
When an astronaut pressed a key or flipped a switch, it did not trigger an asynchronous software interrupt in the modern sense. Unpredictable interrupts destroy real-time determinism. Instead, inputs were routed through **Multiplexer/Demultiplexers (MDMs)** on the shuttle’s serial data buses.

* The **AP-101** flight computers operated as bus commanders.
* At precisely scheduled intervals (e.g., 25Hz, 50Hz, or 100Hz, depending on the flight phase), the HAL/S flight software would command the bus to poll the MDMs.
* The MDMs would reply with the current state of the keyboards, switches, and hand controllers.

## 3. The Software Processing (The UI Tasks)
Once the raw telemetry from the keyboard was in the flight computer's memory, it was processed by the PASS User Interface (UI) modules.

The Shuttle used a highly specific command syntax consisting of three main functions:
* **OPS (Operational Sequences):** Major software modes (e.g., OPS 1 was ascent, OPS 3 was entry).
* **SPEC (Specialist Functions):** Background displays for subsystems (e.g., environmental control or power).
* **ITEM (Data Entry):** The primary way the crew mutated variables. Every parameter on the CRT had an "Item Number" next to it.

If the commander wanted to change a target attitude, they would type: `ITEM 1 4 + 2 5 0 EXEC` (Select Item 14, change value to positive 250, Execute).

Here is how the HAL/S software digested that:
* **Lexical Parsing:** The UI task read the buffer, parsed the `ITEM` command, and validated the syntax.
* **Range Checking:** Before applying any number to the flight software, the UI task checked the input against hardcoded safety limits for that specific variable.
* **The COMPOOL Update:** If valid, the UI task did not directly message the GN&C (Guidance, Navigation, and Control) software. Instead, it used an `EXCLUSIVE` lock to write the new value into the `COMPOOL` (the globally shared memory pool we looked at in previous test cases).
* **Display Refresh:** The UI task then generated the screen drawing commands, formatting the new value into strings, and queued them to be transmitted back out the data bus to refresh the CRT.

## 4. GN&C Ingestion (The Separation of Concerns)
The genius of PASS was the absolute isolation between the UI and the flight control loops. The GN&C tasks (running at high priorities and strict cyclic intervals) never waited for crew input.

When a GN&C task woke up for its 25Hz cycle, it simply read the current value from the `COMPOOL`. If the crew had updated the target attitude in the `COMPOOL` a few milliseconds prior via the UI task, the GN&C task seamlessly ingested the new value and adjusted the thruster firing logic accordingly. If no input had occurred, it kept flying using the old values.

## 5. The Redundancy Management (Voting on Inputs)
During critical flight phases (ascent and entry), PASS ran on four identical AP-101 computers simultaneously, all executing the exact same HAL/S code.

If an astronaut typed a command on Keyboard 1, that hardware was only physically wired to a subset of the data buses. Therefore, only one or two flight computers might actually "hear" the keystroke directly from the MDM.

To solve this, the computers performed an **Input Cross-Tie**. Before processing the keystroke, the computers halted momentarily at a synchronization point, passed their received keyboard inputs to each other over dedicated Inter-Computer Communication (ICC) buses, and voted. Only after all four computers agreed that a specific key was pressed did the HAL/S UI tasks parse the command and update their respective `COMPOOL`s, ensuring all four computers remained perfectly identical in state.
