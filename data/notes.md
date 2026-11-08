Data quality findings

What's good

- Clean and consistent. All 473 relationships point to elements that exist, there are no duplicate names, and Type and Status only use allowed values. The loader's checks never had to reject anything.
- Fully traceable. All 342 elements and 473 relationships cite a P180 figure and page. 60 elements are cited from several figures, and 197 relationships have explanatory notes.
- Honest about uncertainty. The one uncertain link (Baggage Tracking System to Tracking Platform) is marked Inferred, with a note explaining the naming difference.

So the data is accurate but thin. Everything in it is reliable, but it lacks the structure an impact analysis needs most.

What's weak (measured)

┌───────────────────────────────────────────┬───────────────────────┬────────────────────────────────────────────────────────────┐
│                  Finding                  │        Number         │                     Effect on answers                      │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ No dependencies between applications      │ 0 Depends On links    │ The most important impact, systems calling or feeding the  │
│                                           │                       │ replaced one, is always "unknown"                          │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ Applications without a platform           │ 31 of 49              │ Technology impact is unknown for nearly two-thirds of      │
│                                           │                       │ applications                                               │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ Applications where every capability would │ 44 of 49              │ The main risk signal barely tells applications apart       │
│  be left uncovered                        │                       │                                                            │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ Capabilities realized by more than one    │ 1 of 134 (Create      │ No sense of overlap or redundancy in the landscape         │
│ application                               │ Reservation)          │                                                            │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ Applications linked directly to data      │ 0                     │ Data impact can only be worked out per domain              │
│ objects                                   │                       │                                                            │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ Distinct connected-domain lists across 49 │ 9, one per domain     │ Every application in a domain gets the same list, which is │
│  applications                             │                       │  generic rather than specific                              │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ Capabilities placed in a subdomain        │ 0 of 134              │ Uncovered capabilities can't be grouped by business area   │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ Subdomains with no application            │ 16 of 42              │ Parts of the business have no systems in the model         │
├───────────────────────────────────────────┼───────────────────────┼────────────────────────────────────────────────────────────┤
│ Applications mapped to no capabilities    │ 3                     │ No business impact for the Group Sales Tool family         │
└───────────────────────────────────────────┴───────────────────────┴────────────────────────────────────────────────────────────┘

Root cause: P180 is a reference architecture. It describes a generic airline at the level of which application supports which capability. It was never meant to document integrations, data flows or deployment. The Read Me also deliberately left out the business capability maps and process figures (Figs. 3, 4, 8, 9 and so on), which would have added capability structure.

How to improve the feedback

A. Improve the data (team changes the Excel, per build rule 2)

1. Add application dependencies, the biggest win. Add Depends On rows marked Illustrative for the Lufthansa case, or Documented where Lufthansa confirms them. The engine already supports this: every answer would gain a dependency section with labels taken from each link's Status, with no code changes.
2. Add which data each application uses. A new relationship type (for example "Uses", application to data object) would make connected domains specific to each application instead of a copy of the domain's list. The new type has to be added to the Read Me and to the loader's list of allowed values.
3. Load the capability maps that were left out. Linking capabilities to subdomains would let answers group losses by business area ("4 Reservation Ticketing capabilities and 2 Search capabilities would be lost") instead of a flat list.
4. Fill in missing platforms where P180 allows it. Some of the 31 are probably naming differences like the Baggage Tracking case. Those can be added as Inferred, with a note.
5. Add aliases. An alias column (for example DCS, CUPPS, ACARS from the P180 abbreviations list on printed pages 90–92) would fix the abbreviation weakness in name resolution.

B. Improve how answers present the data (code changes)

1. Lead with what matters. Put uncovered capabilities and gaps at the top. Move connected domains into a collapsed "context" section, and drop data objects that appear in 4 or more domains (Customer, Flight, Offering and similar), because they connect almost everything.
2. Show a data completeness bar per answer, for example "Capabilities ✓ · Platform ✗ · Dependencies ✗ · Data usage ✗". It would show at a glance how far the architect can trust the answer.
3. Better evidence. Show a crop of the P180 figure with the element highlighted, rendered with PyMuPDF, instead of the jumbled figure text. That's much more convincing to an architect.
4. Check meaning, not just names. Add a check that each sentence mentions the elements it cites. Or let a second model call score each sentence against its findings, and regenerate on a mismatch.
5. Collect user feedback. Add thumbs up/down to each answer (st.feedback) with an optional comment, stored in Neo4j or a log file. That gives the evaluation a human quality measure alongside the automatic ones.

Priority

┌──────────┬───────────────────────────────────────────────────────────────────┬─────────────────────────┬──────────────────────┐
│ Priority │                              Change                               │           Who           │        Effort        │
├──────────┼───────────────────────────────────────────────────────────────────┼─────────────────────────┼──────────────────────┤
│ 1        │ Illustrative dependencies for the demo case                       │ Team (Excel)            │ Low; the code is     │
│          │                                                                   │                         │ ready                │
├──────────┼───────────────────────────────────────────────────────────────────┼─────────────────────────┼──────────────────────┤
│ 2        │ Put uncovered capabilities and gaps first, collapse connected     │ Code                    │ Low                  │
│          │ domains                                                           │                         │                      │
├──────────┼───────────────────────────────────────────────────────────────────┼─────────────────────────┼──────────────────────┤
│ 3        │ Data completeness bar                                             │ Code                    │ Low                  │
├──────────┼───────────────────────────────────────────────────────────────────┼─────────────────────────┼──────────────────────┤
│ 4        │ Aliases (abbreviations)                                           │ Excel + small code      │ Low to medium        │
│          │                                                                   │ change                  │                      │
├──────────┼───────────────────────────────────────────────────────────────────┼─────────────────────────┼──────────────────────┤
│ 5        │ Application-to-data-object links                                  │ Excel + small code      │ Medium               │
│          │                                                                   │ change                  │                      │
├──────────┼───────────────────────────────────────────────────────────────────┼─────────────────────────┼──────────────────────┤
│ 6        │ Figure crops as evidence                                          │ Code                    │ Medium               │
├──────────┼───────────────────────────────────────────────────────────────────┼─────────────────────────┼──────────────────────┤
│ 7        │ Thumbs up/down feedback                                           │ Code                    │ Low                  │
└──────────┴───────────────────────────────────────────────────────────────────┴─────────────────────────┴──────────────────────┘

Items 1 to 3 together would most change how the demo feels. The Booking System answer would show real dependencies and its data completeness at a glance, with less generic noise.

A note on scope: the plan lists "any data outside P180" as out of scope. Items 1 and 5 would bring in Lufthansa or team-added data, which the Status values (Illustrative/Documented) are designed to label. Present them as next steps or as clearly labelled additions, not as part of the P180-only proof of concept.