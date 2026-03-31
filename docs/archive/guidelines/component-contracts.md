# Component Contracts — Input Accountability

When a processing component silently skips inputs without recording the decision, every consumer must replicate internal skip logic to get correct counts. Dashboards break. Monitoring undercounts. Audits miss items. The root cause: the component is a **partial function** — undefined for inputs it silently drops.

**Core principle:** Every processing component is a total function over its input domain. Every input it receives must exit with an explicit disposition: processed, skipped (with reason), or errored (with details). No input may be silently dropped.

Grounded in: Design by Contract (Meyer), Total Functional Programming (Turner), information hiding (Parnas). The completeness invariant — `processed + skipped + errored = total_inputs` — appears identically in HTTP (every request gets a response), gRPC (UNIMPLEMENTED over silence), Airflow (SKIPPED as first-class terminal state), and exhaustive pattern matching (Rust/Haskell reject unhandled cases).

## Checklist

When adding or modifying a component that processes a set of inputs (pipeline stage, batch processor, request handler, data transformer):

1. **Define the input scope explicitly.** What items does this component receive? If it queries a database, the WHERE clause IS the scope — document it.
2. **Account for every item in scope.** For each input, the component must produce exactly one of: a result (processed), a skip record with reason, or an error record with details.
3. **Record skips, don't filter silently.** If the component decides not to process an item (wrong domain, unsupported format, precondition not met), write a skip record with the reason. The decision is data, not an implementation detail.
4. **Make the scope queryable without internal knowledge.** A consumer (dashboard, monitor, downstream stage) must be able to determine the component's processing state by querying its outputs alone — without knowing skip lists, domain filters, or routing rules.
5. **Verify the completeness invariant.** `processed + skipped + errored` must equal the total input count. If it doesn't, items were silently dropped.
6. **Treat "not applicable" as a skip, not an absence.** An item that enters scope but doesn't need processing (e.g., a YouTube URL in a text-extraction stage) must be explicitly marked as skipped — not excluded from the query and forgotten.

## Anti-patterns

- **Silent domain filtering.** A query excludes certain domains from processing, but no skip record is created for excluded items. Consumers must duplicate the domain list to get correct counts. Fix: write a skip record for each filtered item, or narrow the input scope so filtered items never enter it.
- **Implicit source-type routing.** A component only processes items of certain source types but accepts all types in its interface. Items of other types pass through with no record. Fix: either narrow the interface (precondition) or record skips for unhandled types.
- **Null-as-skip ambiguity.** A NULL output column means both "not yet processed" and "intentionally skipped." Consumers cannot distinguish pending work from completed decisions. Fix: use a separate status or error field to record skip reasons.
- **Try/except with continue.** A batch processor catches per-item exceptions and continues, but doesn't record which items failed or why. Aggregations silently undercount. Fix: log every exception with item identifier and write an error record.
