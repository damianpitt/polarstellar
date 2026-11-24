# Interactive native release acceptance record

Copy this template into a local review file for each target. Keep personal notes and example
investigation files outside Git. Record actual results; an automated runner result alone
does not fill an interactive check. All rows below begin **Not run**.

PolarStellar is an open-source project, not affiliated with the Stellar Development Foundation.

## Identify the exact candidate

| Field | Observed value |
| --- | --- |
| Review date/time and reviewer | Not recorded |
| Operating system/version and desktop environment | Not recorded |
| Processor architecture | Not recorded |
| Clean machine/VM state and preinstalled software | Not recorded |
| Download origin and archive filename | Not recorded |
| Archive SHA-256 verified against the matching checksum | Not run |
| BUILD_INFO version and source commit | Not recorded |
| Visible application version | Not recorded |
| Source/native/fresh-runner validation links | Not recorded |

Targets are Linux x86_64, macOS Apple Silicon and Windows x86_64. Mac Intel is excluded.
Use a disposable OS user/VM where possible. Launch the extracted app without installing
Python or project libraries. Preserve the bundled libraries beside the executable.

## Interactive checks

| Check | What to observe | Result / evidence |
| --- | --- | --- |
| Downloaded-app launch | Normal OS approval/quarantine prompts; no global security-setting changes | Not run |
| Runtime prerequisites | App launches with documented OS libraries and no project Python environment | Not run |
| Window sizes | All pages readable at normal and compact sizes; scrolling reaches controls and evidence | Not run |
| Account lookup | Public account on its chosen network; exact amounts, issuer and authority/source details readable | Not run |
| Loading and cancellation | Search/cancel/network switch retain correct context; late data does not repaint another search | Not run |
| Provider failures | Useful error/retry behavior for unavailable identifiers, lost connectivity and unavailable history | Not run |
| Activity and transaction | Independent Load more, failed labels, selected transaction, operation evidence and effects | Not run |
| Graph interactions | Drag, zoom, fit, selection, evidence links and bounded expansion; source/coverage remain visible | Not run |
| Timeline/filter controls | Applied time/type filters, UTC chronology, timestamp ambiguity and evidence agree | Not run |
| Assets and contracts | Exact issuer/network, SAC, events/specification and explicitly supplied storage keys | Not run |
| Native export destination dialog | Cancel writes nothing; save CSV/JSON; overwrite choice and permission errors work | Not run |
| Export evidence | Exact amounts, network/source, coverage and selected annotation preference survive | Not run |
| Notes and offline reopening | Save disposable notes/labels; close/relaunch offline; evidence and annotations survive | Not run |
| Watchlists | Mix saved networks/types; reopen, filter, import/export, manually refresh and compare | Not run |
| Unsaved edits and closing | Refuse discard and preserve drafts; accept discard; reopen committed data | Not run |
| Local data/cache | Cache begins off; clear only affects cache; replacing app keeps saved libraries | Not run |
| Desktop accessibility | Keyboard navigation, focus, scaling, fonts and contrast usable | Not run |

Only use public identifiers and explicitly chosen public storage keys. No private key, wallet
creation, signing or transaction submission is needed. A failed/empty endpoint result does
not prove a resource never existed; record provider, time and history limits.

## Findings and disposition

Record each reproducible issue, steps, observed/expected behavior, platform, exact candidate
and severity. Keep personal exports/screenshots local. Link a fix and repeat the affected
check before marking it passed. Unavailable environments remain **Not run**.

Final disposition: **Pending interactive acceptance**. Record limitations before promoting
a candidate to V0.1. Signing/notarization and system installers have their own scope.
