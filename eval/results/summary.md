# Evaluation results

Run on 2026-11-02 20:41 with 20 questions (parsing model `gpt-5.4-mini`, answer model `gpt-5.4`).

## Summary

| Measure | Result |
| --- | --- |
| Questions with the expected behaviour | 20 / 20 |
| Application identified correctly | 14 / 14 |
| Expected capabilities found (recall) | 100% |
| Returned capabilities that are correct (precision) | 100% |
| Expected uncovered capabilities found (recall) | 100% |
| Returned uncovered capabilities that are correct (precision) | 100% |
| Answers naming every uncovered capability | 14 / 14 |
| Statements carrying a correct label | 137 / 137 (100%) |
| Documented facts with a page reference | 32 / 32 (100%) |
| Answers naming no element outside the analysis | 14 / 14 |
| Answers written by the LLM (not the fallback summary) | 14 / 14 |
| Same findings for differently phrased questions | 1 / 1 applications |
| Ambiguous or partial names: choices offered, no guess | 3 / 3 |
| Unknown names: asked again, no analysis | 2 / 2 |
| Unsupported questions declined | 1 / 1 |
| Errors | 0 |
| Response time, mean / max (seconds) | 9.2 / 13.8 |

## Per question

| Id | Question | Expected | Actual | OK | Capability recall | Uncovered recall | Labelled | Facts with pages | Time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Q01 | What would be affected if we replaced the Booking System? | answer | answer | yes | 100% | 100% | 9/9 | 2/2 | 13.0 |
| Q02 | If we retire our booking system, what breaks? | answer | answer | yes | 100% | 100% | 10/10 | 3/3 | 13.8 |
| Q03 | What is the impact of switching off the Direct Sales Platform? | answer | answer | yes | 100% | 100% | 10/10 | 3/3 | 13.6 |
| Q04 | What would be affected if we replaced the Product Maintenance Application? | answer | answer | yes | 100% | 100% | 9/9 | 2/2 | 12.9 |
| Q05 | What happens if we decommission the Notifying Service? | answer | answer | yes | 100% | 100% | 11/11 | 2/2 | 11.4 |
| Q06 | What would be affected if we replaced the Baggage Tracking System? | answer | answer | yes | 100% | 100% | 8/8 | 1/1 | 13.3 |
| Q07 | What would be affected if we replaced the Fare Engine? | answer | answer | yes | 100% | 100% | 9/9 | 1/1 | 13.1 |
| Q08 | What breaks if the Crew Management System is replaced? | answer | answer | yes | 100% | 100% | 9/9 | 2/2 | 10.1 |
| Q09 | What would be affected if we replaced the Cargo Forwarding System? | answer | answer | yes | 100% | 100% | 10/10 | 3/3 | 13.0 |
| Q10 | What would be the impact of replacing the Customer Data Manager? | answer | answer | yes | 100% | 100% | 11/11 | 4/4 | 12.3 |
| Q11 | What would be affected if we replaced the Load Planning Tool? | answer | answer | yes | 100% | 100% | 9/9 | 2/2 | 12.5 |
| Q12 | What would be affected if we replaced Accounting? | answer | answer | yes | 100% | 100% | 10/10 | 3/3 | 9.5 |
| Q13 | What would be affected if we replaced the Group Sales Tool? | answer | answer | yes | n/a | n/a | 10/10 | 1/1 | 13.3 |
| Q14 | What would be affected if we replaced the Passenger and Baggage Processing Application / Departure Control System? | answer | answer | yes | 100% | 100% | 12/12 | 3/3 | 12.0 |
| Q15 | What happens if we replace the booking tool? | clarify_with_options | clarify_with_options | yes | – | – | – | – | 1.6 |
| Q16 | What if we replace the departure control system? | clarify_with_options | clarify_with_options | yes | – | – | – | – | 1.7 |
| Q17 | What if we replace the crew system? | clarify_with_options | clarify_with_options | yes | – | – | – | – | 1.7 |
| Q18 | What would be affected if we replaced the Quantum Teleporter? | clarify_unknown | clarify_unknown | yes | – | – | – | – | 1.9 |
| Q19 | What would be affected if we replaced Create Reservation? | clarify_unknown | clarify_unknown | yes | – | – | – | – | 1.7 |
| Q20 | Which applications belong to the Sales domain? | unsupported | unsupported | yes | – | – | – | – | 1.5 |
