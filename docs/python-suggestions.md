# Python suggestion syntax checks

Candidate assessment checks Python suggestions locally after validating the model's
evidence. The check also applies to saved targeted assessment retries and exact
repeats through the shared assessment validator. It compiles text in memory with the
standard library and never executes analyzed code or writes to its checkout.

For `.py` and `.pyi` files, the candidate must refer to the current pinned revision
and its `existing_code` must exactly match the complete `start_line`–`end_line`
range, preserving indentation. A terminal newline may be omitted. The suggestion
replaces those lines in the complete pinned file, preserving the boundary before
the next untouched line. A syntactically valid original that becomes invalid marks
`suggestion_supported` false. The confirmed finding remains and existing report
builders omit its `suggestion_code`; the explanation records why it was withheld.
Candidate snapshots and original saved reports remain unchanged.

An already invalid original, a mismatched or missing snippet/range/source, a stale
revision, a renamed path, another file type or a parser resource failure does not
establish that the suggestion introduced a syntax error. These cases preserve the
assessor's decision. No patch application is guessed. This guard uses the running
Python interpreter's grammar; syntax accepted only by a different Python version
may therefore remain unchecked. Successful compilation does not establish runtime
behavior, imports, type correctness or safety of the suggested change.
