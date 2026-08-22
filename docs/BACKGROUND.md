# Background: HAL/S, the Shuttle flight software, and the name

## About HAL/S

HAL/S (*High-order Assembly Language / Shuttle*) is a real-time aerospace
programming language developed by Intermetrics for NASA in the early 1970s. It
was the primary language for the Space Shuttle's flight software and is one of
the most successful safety-critical languages ever fielded, flying human-rated
avionics for roughly three decades.

HAL/S was purpose-built for guidance, navigation, and control software, and its
design reflects that domain:

- **Aerospace-native types.** Beyond `INTEGER`, `BOOLEAN`, and `CHARACTER`, the
  language has first-class `SCALAR` (floating point), `VECTOR`, `MATRIX`, and
  `BIT` (bit-string) types, so linear-algebra-heavy flight math reads naturally.
- **Built-in vector/matrix arithmetic.** Dot and cross products, matrix
  multiplication, transpose, and inverse are language-level operations rather
  than library calls.
- **Real-time scheduling.** `SCHEDULE`, `TASK`, `UPDATE`, `WAIT`, and
  `PRIORITY`, with `EVERY`/`AFTER`/`AT` timing, express the cyclic and
  priority-driven execution model of a flight computer.
- **Shared data pools.** `COMPOOL` blocks define shared, inter-process data —
  the coordination mechanism between independently scheduled processes.
- **Structured error handling.** `ON ERROR` handlers respond to numbered runtime
  faults, supporting the fault detection and recovery that human-rated systems
  demand.
- **Register-level control.** `BIT` types and `$` bit-subscripting give direct,
  explicit access to hardware bits and status registers.
- **A distinctive multi-line source form.** HAL/S source could be written across
  stacked lines — a main line with exponent and subscript annotation lines above
  and below — so code resembled the mathematical notation it implemented. (A
  conventional single-line form is also supported, and is what Resnik targets.)

Designed decades before "memory safety" was a common term, HAL/S emphasized
strong typing, explicit real-time semantics, and verifiability — the same
properties that make Rust a fitting modern compilation target.

## HAL/S deployments

- Space Shuttle: Flight Software (FSW) for the Space Shuttle's onboard computers, including the Primary Avionics Software System (PASS) and the Backup Flight Software (BFS).

- Galileo: Deployed Flight Software (FSW) for the Attitude and Articulation Control Subsystem (AACS). The Galileo spacecraft launched on October 18, 1989, while the first satellites for the European Galileo Navigation System lifted off on December 28, 2005.

## Programming-language trivia
The "HAL" name was chosen to honor Hal Laning, a pioneer in aerospace computing at MIT. The "S" was added to stand for "Shuttle"

## Space Shuttle flight-software history

The software Resnik's HAL/S heritage traces back to is the **Primary Avionics
Software System (PASS)** — the onboard flight software of the Space Shuttle, and
one of the most rigorously engineered programs of its era. Developed by IBM's
Federal Systems Division, PASS ran the orbiter's guidance, navigation, and
control in HAL/S, with its operating system (the Flight Control Operating
System) and low-level routines in assembly.

It flew on a set of five **General Purpose Computers (GPCs)** — IBM AP-101
machines from the System/4 Pi family, close cousins of the System/360
mainframe. Four ran PASS as a synchronized, voting redundant set, cross-checking
each other hundreds of times per second and voting out any computer that
disagreed. The fifth ran the **Backup Flight System (BFS)** — deliberately
developed by a separate contractor (Rockwell) so that a single software fault
could not take down primary and backup at once. That "diverse redundancy"
philosophy — guard against common-mode software failure, not just hardware
failure — is a recurring theme in safety-critical avionics. Because the AP-101's
memory couldn't hold everything at once, PASS was segmented into eight
phase/function memory loads streamed in from a tape-based mass-memory unit as
the mission moved through ascent, orbit, and entry. The AP-101S upgrade (first
flown on STS-37 in 1991) swapped core memory for semiconductor memory and
roughly doubled capacity.

Its most famous moment came before the program ever left the ground: the STS-1
launch scrub of April 10, 1981, when the backup computer refused to synchronize
with the four primaries — Jack Garman's *"Bug Heard 'Round the World."* PASS
flew for three decades, retiring with the Shuttle in 2011.

**Further reading** (in [`docs/Space_Shuttle_Flight_Software_History/`](Space_Shuttle_Flight_Software_History/)):

- [The 1977 Approach and Landing Tests](Space_Shuttle_Flight_Software_History/Enterprise-1977-Approach_And_Glide_Test.md)
  — the Enterprise glide trials that proved PASS digital fly-by-wire.
- [STS-1: "The Bug Heard 'Round the World"](Space_Shuttle_Flight_Software_History/STS-1-The-Bug-Heard-Round-The-World.md)
  — the ~1-in-67 timing bug that scrubbed the first launch.
- [STS-9: Common-Mode Hardware Failure](Space_Shuttle_Flight_Software_History/STS-9-Incident-Common_Mode_Hardware_Failure.md)
  — two GPCs lost to a loose-solder fault during entry, and redundancy
  management against a shared hardware defect.
- [PASS Operational Sequences (OPS)](Space_Shuttle_Flight_Software_History/PASS-Operational_Sequences.md)
  — the OPS memory configurations that reconfigured the GPCs for each flight
  phase (ascent, orbit, entry).
- [PASS Crew Input Processing](Space_Shuttle_Flight_Software_History/PASS-Crew-Input-Processing.md)
  — how the crew drove the system through 32-key keypads and the
  `OPS`/`SPEC`/`ITEM` display language.

## The compiler's namesake

Judith Resnik (April 5, 1949 – January 28, 1986) was an American electrical engineer, pilot, and NASA astronaut who became a trailblazer in space exploration.
Born in Akron, Ohio, to Jewish parents of Russian immigrant background—her father an optometrist and her mother a legal secretary—Resnik showed exceptional talent in math and science from a young age. She graduated as valedictorian from Firestone High School and earned a perfect math SAT score. She received her B.S. in electrical engineering from Carnegie Mellon University (then Carnegie Tech) in 1970 and later a Ph.D. in electrical engineering from the University of Maryland in 1977.
Her early career included work at RCA in missile and radar systems, biomedical engineering research at the National Institutes of Health, and engineering roles at Xerox. A licensed pilot, she was selected by NASA in January 1978 as part of Astronaut Group 8, the first class to include women.
Resnik became the second American woman in space (after Sally Ride) and the first Jewish woman of any nationality to fly in space. On her first mission, STS-41-D aboard Space Shuttle Discovery (August–September 1984), she served as a mission specialist, operating the robotic arm (Canadarm) and helping deploy satellites, logging about 145 hours in orbit total.
Tragically, she was killed along with the entire crew during her second mission, STS-51-L aboard Space Shuttle Challenger, when the vehicle exploded 73 seconds after launch on January 28, 1986.
Resnik was remembered as brilliant, determined, and multifaceted—she was also a classical pianist who enjoyed running, bicycling, and flying. Her legacy endures through memorials, including a lunar crater named after her, and she remains an inspiration for women and minorities in STEM and spaceflight.
