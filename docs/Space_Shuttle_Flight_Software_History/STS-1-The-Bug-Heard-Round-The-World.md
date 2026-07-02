# STS-1: "The Bug Heard 'Round the World"

The Space Shuttle's flight software very nearly made its public debut by *not*
letting the vehicle fly. The STS-1 launch scrub is one of the most retold
cautionary tales in software engineering — and it played out on the pad, in
front of the world.

> Resnik compiles HAL/S, the language the Shuttle's primary flight software was
> written in. This is a story about that software.

---

## The scrub

**April 10, 1981.** Columbia sat on Pad 39A for the first launch of the Space
Shuttle program, John Young and Robert Crippen strapped in. When the countdown
resumed from the planned hold at T−20 minutes, controllers commanded the
computers from vehicle-checkout mode into flight-configuration mode. The Data
Processing System officer, Brock "Randy" Stone, watched the four primary General
Purpose Computers (GPCs) transition correctly and the fifth — the backup — come
up as well. But the backup computer could not communicate with two of the four
primaries.

A second attempt at the T−9 hold failed the same way. With no immediate fix,
Flight Director Neil Hutchinson called it no-go and Launch Director George Page
halted the count. Young and Crippen climbed back out of Columbia after nearly
six hours in the cockpit. Launch slipped two days.

---

## The bug

The primary flight software (**PASS**) ran on four GPCs as a synchronized,
redundant set; the backup flight software (**BFS**) ran independently on the
fifth, shadowing the primaries so it could take over on command. For that
handoff to be possible, the BFS had to lock onto the primaries' cyclic timing
precisely.

The fault traced to a change made about a year earlier, in what looked like a
completely unrelated part of the system. It had quietly opened a narrow timing
window — roughly a **1-in-67** chance — during computer initialization. When the
first primary GPC was powered up some 30 hours before launch, it happened to
land *inside* that window, and a critical cyclic process came up phased one cycle
later than the software expected. The result was a small timing skew (about
40 milliseconds) that left the backup — powered up only ~20 minutes before
launch — unable to build a correct picture of the primary set, and therefore
unable to synchronize.

The failure was, mercifully, benign: nothing was actually broken. Power the
computers down and back up, and initialization would almost certainly fall
*outside* the window. That is exactly what happened — Columbia launched
flawlessly two days later, on **April 12, 1981.**

---

## Why it became famous

Jack Garman, a NASA engineer at the Johnson Space Center, wrote it up for *ACM
Software Engineering Notes* in 1981 under the title that stuck: "The Bug Heard
'Round the World." It endures as a teaching example because it captures several
hard truths at once:

- **Change is where risk hides.** A seemingly innocuous, unrelated modification a
  year prior created the latent fault.
- **Probabilistic bugs are the worst kind.** A ~1-in-67 window means the system
  behaves correctly about 66 times out of 67 — nearly invisible in testing, and
  unnerving in production.
- **Complexity creep is real.** As a system grows, the interactions that can
  produce a fault multiply faster than any one engineer can hold in mind.
- **Diverse redundancy has limits.** The independently developed backup guarded
  against a *shared logic* fault — but what bit here was a timing and
  synchronization interaction *between* the primary and backup systems, which
  neither could have caught alone.

---

## Relevance to Resnik

Resnik targets Rust partly to move whole classes of defect to *compile time* —
the memory-safety and data-race errors the borrow checker rejects outright.
STS-1 is a humbling reminder of both what that buys and what it doesn't: no type
system catches a probabilistic initialization-timing skew between independently
scheduled processes. Those bugs live in the *system*, and they're found by
review, analysis, and hard-won operational experience — the same disciplines
that nonetheless made PASS one of the most reliable programs ever fielded.

---

*Sources: Jack Garman, "The Bug Heard 'Round the World" (ACM SIGSOFT Software
Engineering Notes, 1981); NASA JSC STS-1 histories and mission records.
Paraphrased; consult those works for authoritative detail.*
