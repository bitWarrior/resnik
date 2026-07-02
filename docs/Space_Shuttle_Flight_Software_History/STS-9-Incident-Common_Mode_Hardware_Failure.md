# The STS-9 Incident: When a Common-Mode Defect Meets Redundancy Management

STS-9 (Spacelab-1) — flown by *Columbia* in late 1983 — is one of the most
sobering real-world tests the Space Shuttle's Primary Avionics Software System
(PASS) ever faced. It is sometimes retold as a tidy redundancy-management
success, but the more accurate — and more useful — lesson is nearly the
opposite: STS-9 showed how a *common-mode hardware defect* can erode the very
redundancy that flight software leans on, and how much still came down to human
judgment.

## 1. The incident

On December 8, 1983, during deorbit and entry preparation — roughly five hours
before the scheduled landing — one of the four primary General Purpose Computers
(GPCs) failed as a nose Reaction Control System (RCS) jet fired. About six
minutes later, following another jet firing, a second GPC failed. The first
could not be recovered at all; the second was coaxed back online, only to fail
again at touchdown.

Commander John Young chose not to rush it. He delayed the landing and let the
orbiter drift while the team worked the problem, ultimately bringing *Columbia*
down at Edwards Air Force Base nearly eight hours later than planned. His account
of watching the second machine drop was famously visceral — knees shaking, as he
put it, turning to jelly.

## 2. Forensic diagnosis: a loose sliver of solder

Post-flight analysis traced the failures to a piece of solder that the RCS
thruster vibration had shaken loose, shorting a GPC's CPU board. Because every
GPC was built to the same standard, they all shared the same latent
manufacturing vulnerability — a **common-mode** susceptibility. The jet firings
did not fail all four at once; they independently triggered the same class of
defect in two of them, minutes apart.

## 3. Why this was *not* a clean redundancy save

The comfortable version of the story — Redundancy Management (RM) spots the
divergence, votes out the bad computers, and the survivors fly on untroubled —
is only half right, and the missing half is the important part.

* **Yes**, the redundant set kept flying the vehicle on its remaining healthy
  GPCs, exactly as designed.
* **But the defect was common-mode.** The fifth computer, running the
  independently developed Backup Flight System (BFS), was built the same way and
  could carry the same flawed joint. Young later testified that activating the
  backup at that point would have meant *"loss of vehicle and crew"* — falling
  back to a single suspect BFS GPC was arguably more dangerous than staying on
  the wounded primary set.
* **The voting margin was nearly gone.** Isolating a faulty computer by majority
  vote needs at least three healthy participants. With two of four primaries
  lost, the set was one failure away from being unable to tell *which* computer
  was wrong — it could detect a disagreement but not resolve it.

RM bought margin, but the margin was thin, and the fallback that was supposed to
be the ultimate safety net was itself under suspicion. The crew's survival owed
as much to Young's decision to slow down and *not* reach for the BFS as to any
voting algorithm.

## 4. Engineering and operational response

* **A sixth computer.** Beginning with the next flight (STS-41B, February 1984),
  the orbiter carried a sixth GPC as an onboard spare, so a depleted set could be
  rebuilt in flight rather than flown to the edge.
* **Manufacturing scrutiny.** The loose-solder finding drove tightened
  inspection and workmanship standards for the GPC boards.
* **Respect for common-mode faults.** STS-9 became a standing reminder that
  identical redundant hardware shares identical defects — the very reason the
  BFS was written by a separate team on separate principles, and a caution that
  even *that* measure has limits.

*(STS-9 was not a quiet day otherwise, either: it also suffered an
inertial-measurement-unit failure, and — unnoticed until after wheelstop — a
hydrazine leak set two of the three auxiliary power units on fire. The orbiter
landed safely regardless.)*

## 5. Relevance for flight software development

The durable principle here is **not** "PASS redundancy management saved the
day." It is subtler and more valuable: **redundancy defends against independent
failures, not shared ones.** Voting, cross-ties, and hot spares multiply
reliability only insofar as the redundant elements fail *independently* —
identical hardware, identical software, or a shared environment can quietly
violate that assumption. Good flight software already treats its hardware as an
untrustworthy data source: constant verification, cross-checks, and autonomous
isolation. STS-9 adds the harder lesson that no amount of software voting can
out-vote a defect present on every ballot. Covering the common-mode gap takes
*diversity* — independent implementations, dissimilar hardware, and a human in
the loop empowered to slow down.

---

*Sources: NASA and ESA STS-9 / Spacelab-1 mission records; STS-9 mission report
and Wikipedia; Ken Shirriff's history of IBM's System/4 Pi computers; John Young's
post-flight testimony. Paraphrased; consult those works for authoritative detail.*
