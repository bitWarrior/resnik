# Space Shuttle PASS: Operational Sequences (OPS)

The Primary Avionics Software System (PASS) was modular. The highest level of its
applications software was the **Operational Sequence (OPS)** — a self-contained
memory load, streamed into a General Purpose Computer (GPC) from the tape-based
Mass Memory Unit, holding exactly the software needed for one part of the
mission. Because the AP-101's memory could not hold everything at once, the crew
swapped OPS loads as the flight progressed. Each OPS was further divided into
**Major Modes (MM)** — sub-phases within it — and every OPS transition was
initiated by the crew (e.g. `OPS 301 PRO` to load the entry software).

## OPS are namespaced by major function

The subtlety that trips up tidy summaries: OPS numbers are **not** a flat 0–9
list with one mission phase per digit. Each OPS is scoped to a **major
function** — **GNC** (Guidance, Navigation & Control), **SM** (Systems
Management), or **PL** (Payload) — selected by the `MAJ FUNC` switch. The same
digit can mean different things under different major functions (there is a GNC
OPS 2 *and* an SM OPS 2), and only a handful of OPS numbers were ever flown. The
flight-critical spine lived under GNC.

### GNC — the flight-critical sequence

| OPS | Phase | Notes |
| :-- | :---- | :---- |
| **OPS 1** | **Ascent** | Major modes 101–106: prelaunch/terminal count, first and second stage, and orbital insertion. Uniquely, OPS 1 also *contains* **OPS 6** — there is no time to load new software for a return-to-launch-site abort. |
| **OPS 2** | **On-orbit** | Orbital coast and maneuvers (rotations, translations, OMS burns). |
| **OPS 3** | **Entry & landing** | Major modes 301–305: deorbit prep and burn, entry interface, TAEM, and approach/landing — all the way to wheelstop. **There is no separate "landing OPS."** |
| **OPS 6** | **RTLS abort** | The return-to-launch-site abort profile, resident *inside* the ascent load. |
| **OPS 8** | **On-orbit checkout** | Flight-control-system (FCS) checkout, typically flown before entry. |

### SM — systems management (non-flight-critical)

| OPS | Purpose |
| :-- | :------ |
| **SM OPS 2** | On-orbit systems management: subsystem and payload monitoring/management. Runs on a GPC *outside* the synchronized GNC redundant set. |

### Utility

| OPS | Purpose |
| :-- | :------ |
| **OPS 0** (OPS 000) | GPC operating-system / idle mode: memory configuration, memory read/write, and dumps — the "GPC MEMORY" utility display, not a flight phase. |

*(Other OPS numbers appear in various checklists and major-function overlays, but
they were not the clean phase-per-digit scheme a `0`–`9` table implies. The set
above is the well-documented, flown core — not an exhaustive enumeration.)*

## Important distinctions

- **OPS vs. STS.** *STS* (Space Transportation System) numbers identify the
  *mission*; *OPS* identifies the *software configuration* running on the GPCs at
  a given moment. STS-9 the flight cycled through many OPS loads over its ten
  days.
- **State survives the swap.** Loading a new OPS overlaid the applications
  software, but the navigation state vector and other critical parameters held in
  the `COMPOOL` had to be preserved across the transition. Keeping that shared
  data intact while the phase-specific modules were swapped out was a core job of
  the system software (the Flight Control Operating System).

---

*Sources: Space Shuttle Data Processing System documentation; NASA SP-504 (Space
Shuttle Avionics System); NASA GN&C development histories; Gene Carlow,
"Architecture of the Space Shuttle Primary Avionics Software System."
Paraphrased; consult those works for authoritative detail.*
