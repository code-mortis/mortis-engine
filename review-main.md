# Mortis review

- **Schema:** mortis\.review/v1
- **Status:** partial
- **Run id:** 11b92961eb944b1cbd2c1f88944e8a85
- **Provider:** opencode\-go
- **Model:** muse\-spark\-1\.3\-contributor
- **Mode:** all
- **Revision:** 205b77eae8a20a4c4f76d62d9c9e362c90c58197
- **Base sha:** a95081ab7aa30c554d6cbae76b9c79d737daca53
- **Merge base:** a95081ab7aa30c554d6cbae76b9c79d737daca53
- **Head sha:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
- **Settings sha:** 205b77eae8a20a4c4f76d62d9c9e362c90c58197
- **Workflow status:** completed
- **Elapsed seconds:** 820\.0683839159901

## Change summary

- **Status:** completed
- **Limited context:** True

The snapshot presents Mortis Engine as a local Python package with a mortis command line tool and supporting library modules\. Visible excerpts describe review of local Git revisions, verification of reported evidence against pinned revisions, background summaries, candidate assessment, and offline security scanning\. Supporting areas include saved connection profiles, provider transports, local history storage, immutable reports, and an optional isolated parser for structural context\. The supplied diff creates all listed paths as new files and the excerpts are truncated with limited context enabled, so only initial lines and brief documentation strings are visible and complete behavior cannot be determined\.

### Project packaging and documentation

These files define the installable project, license terms, ignore rules, automation workflow, overview documentation, and planning notes\. Visible excerpts name the package version, Python requirements, install tooling, and main branch workflow, but all file bodies are truncated so complete settings remain unseen\.

- \.github/workflows/ci\.yml
- \.gitignore
- LICENSE
- PLAN\.md
- README\.md
- pyproject\.toml

### Command line entry and commands

This set provides the program entry, argument parsing, and terminal commands for review, scan, saved runs, connection setup, findings triage, followup assessments, and report export\. Excerpts show separation between execution options, profile selection, progress display, and output handling, with details limited to truncated beginnings\.

- src/mortis\_engine/\_\_init\_\_\.py
- src/mortis\_engine/\_\_main\_\_\.py
- src/mortis\_engine/cli/\_\_init\_\_\.py
- src/mortis\_engine/cli/analysis\.py
- src/mortis\_engine/cli/config\.py
- src/mortis\_engine/cli/connect\.py
- src/mortis\_engine/cli/findings\.py
- src/mortis\_engine/cli/followups\.py
- src/mortis\_engine/cli/reports\.py
- src/mortis\_engine/cli/review\.py

### Connection transport and safety helpers

These modules handle saved connection profiles, provider presets, endpoint validation, direct HTTP protocols, pooled dispatch, request admission slots, and shared inference schemas\. Excerpts indicate credential handling, redaction rules, bounded protocols, and destination hashing, but truncation prevents full review of logic\.

- src/mortis\_engine/config\.py
- src/mortis\_engine/gateway\.py
- src/mortis\_engine/llm\_http\.py
- src/mortis\_engine/llm\_bedrock\.py
- src/mortis\_engine/providers\.py
- src/mortis\_engine/discovery\.py
- src/mortis\_engine/schemas\.py
- src/mortis\_engine/security\.py
- src/mortis\_engine/admission\.py
- src/mortis\_engine/workflow\.py

### Local history and operation entry points

This group stores private local run history, immutable task inputs, finding decisions, markdown presentation, candidate scoped assessment retries, and standalone operation functions\. Excerpts describe exact input handling and offline report rendering, with remaining behavior hidden by truncation\.

- src/mortis\_engine/history\.py
- src/mortis\_engine/operations\.py
- src/mortis\_engine/assessments\.py
- src/mortis\_engine/findings\.py
- src/mortis\_engine/reporting\.py

### Pinned review preparation and orchestration

These review modules prepare immutable Git inputs without checkout, plan deterministic assignments, read pinned objects, apply configuration limits, and execute bounded workflows\. Excerpts note credential free Git access and exclusion handling, with full flow only partially visible due to truncated excerpts\.

- src/mortis\_engine/review/\_\_init\_\_\.py
- src/mortis\_engine/review/service\.py
- src/mortis\_engine/review/engine\.py
- src/mortis\_engine/review/execution\.py
- src/mortis\_engine/review/workflows\.py
- src/mortis\_engine/review/planning\.py
- src/mortis\_engine/review/snapshots\.py
- src/mortis\_engine/review/source\.py
- src/mortis\_engine/review/settings\.py

### Review evidence validation and reporting stages

This set covers finding contracts, context sanitization, independent adjudication, evidence selection, verification against original and current revisions, background generation, summaries, reporting scopes, and cancellation signaling\. Excerpts show strict models and source only quotes, with truncated prompts limiting complete description\.

- src/mortis\_engine/review/assessment\.py
- src/mortis\_engine/review/verification\.py
- src/mortis\_engine/review/evidence\.py
- src/mortis\_engine/review/background\.py
- src/mortis\_engine/review/summary\.py
- src/mortis\_engine/review/context\.py
- src/mortis\_engine/review/contracts\.py
- src/mortis\_engine/review/workflow\_contracts\.py
- src/mortis\_engine/review/policy\.py
- src/mortis\_engine/review/file\_policy\.py
- src/mortis\_engine/review/errors\.py

### Isolated structural code context

The code graph package offers optional exact revision structural facts from a disposable containerized parser, with contracts, syntax capabilities, supplied bytes parsing, transport without host mounts, and validation against source bytes\. Excerpts show size limits and language tables, with implementation largely truncated\.

- src/mortis\_engine/code\_graph/\_\_init\_\_\.py
- src/mortis\_engine/code\_graph/build\.py
- src/mortis\_engine/code\_graph/contracts\.py
- src/mortis\_engine/code\_graph/extract\.py
- src/mortis\_engine/code\_graph/syntax\.py
- src/mortis\_engine/code\_graph/transport\.py
- src/mortis\_engine/code\_graph/validation\.py
- src/mortis\_engine/code\_graph/Dockerfile
- src/mortis\_engine/code\_graph/requirements\.txt

### Offline security analysis workflow

These modules implement static security analysis with inert snapshots, scanner normalization, strict response validation, payload references, policy supplements, offline enforcement, advisory lookup, and local workflow service\. Excerpts indicate offline source analysis and evidence handling, with most logic truncated\.

- src/mortis\_engine/security\_scans/\_\_init\_\_\.py
- src/mortis\_engine/security\_scans/advisories\.py
- src/mortis\_engine/security\_scans/context\.py
- src/mortis\_engine/security\_scans/contracts\.py
- src/mortis\_engine/security\_scans/engine\.py
- src/mortis\_engine/security\_scans/inference\.py
- src/mortis\_engine/security\_scans/payload\.py
- src/mortis\_engine/security\_scans/policy\.py
- src/mortis\_engine/security\_scans/sandbox\.py
- src/mortis\_engine/security\_scans/scanner\.py
- src/mortis\_engine/security\_scans/service\.py

### Review test helpers and fixtures

The test area contains package markers, a temporary repository fixture, Git helper functions for isolated commits, and a partial review JSON example\. Excerpts show initialization of a main branch and finding fields, with broader scope not determinable from truncated limited context\.

- tests/code\_graph/\_\_init\_\_\.py
- tests/review/\_\_init\_\_\.py
- tests/review/conftest\.py
- tests/review/fixtures/partial\_review\.json
- tests/review/git\_helpers\.py

## Findings (5)


### 1\. \[medium\] src/mortis\_engine/llm\_http\.py:109\-113

- **Content:** Intermediate creation in \`\_extra\_body\.assign\` allows creating a dict\-shaped \`tools\` value when the request has no tools\. The list\-index guard only applies when \`target\` is already a list, but the \`key not in target\` branch creates \`\{\}\` for missing \`tools\` because \`strict\_flag\` clears \`protected\`\. A dotted \`tools\.0\.strict\` key therefore silently builds \`body\["tools"\]=\{"0":\{"strict":\.\.\.\}\}\` instead of raising invalid tool index\.
- **Category:** correctness
- **Existing code:**

  ```text
                  if isinstance(target, dict) and key not in target:
                      if protected:
                          raise ValueError("extra_body cannot replace protected inference inputs, output limits or authentication")
                      target[key] = {}
                  target = target[key]
  ```

- **Suggestion code:**

  ```text
  else:
      if isinstance(target, dict) and key not in target:
          if protected:
              raise ValueError("extra_body cannot replace protected inference inputs, output limits or authentication")
          if strict_flag and parts[0] == "tools":
              raise ValueError("extra_body references an invalid tool index")
          target[key] = {}
  ```

- **Trigger:** Configure connection \`options\.extra\_body\` with \`\{"tools\.0\.strict": true\}\` \(or \`tools\.0\.function\.strict\`\) and call \`encode\_request\` for a request with \`request\.tools == \[\]\`, so \`body\` has no \`tools\` list\.
- **Consequence:** encode\_request returns a malformed provider body where \`tools\` is a dict \`\{"0": \{"strict": true\}\}\` instead of a list\. The request bypasses the intended invalid\-index rejection and strict validation, causing a downstream provider rejection, wasted inference call, or unexpected payload shape\.
- **Evidence:**
  - **1.**
    - **Revision:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Path:** src/mortis\_engine/llm\_http\.py
    - **Start line:** 93
    - **End line:** 95
    - **Quote:**

      ```text
                  if isinstance(target, list):
                      if not part.isdigit() or int(part) >= len(target):
                          raise ValueError("extra_body references an invalid tool index")
      ```

  - **2.**
    - **Revision:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Path:** src/mortis\_engine/llm\_http\.py
    - **Start line:** 109
    - **End line:** 113
    - **Quote:**

      ```text
                      if isinstance(target, dict) and key not in target:
                          if protected:
                              raise ValueError("extra_body cannot replace protected inference inputs, output limits or authentication")
                          target[key] = {}
                      target = target[key]
      ```

  - **3.**
    - **Revision:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Path:** src/mortis\_engine/llm\_http\.py
    - **Start line:** 181
    - **End line:** 184
    - **Quote:**

      ```text
              if request.tools:
                  body["tools"] = [{"type": "function", "function": {
                      "name": tool.name, "description": tool.description, "parameters": tool.parameters,
                      "strict": _strict_schema(tool.parameters)}} for tool in request.tools]
      ```

- **Source sha:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
- **Inline line:** 113
- **Id:** finding\-3d778a2b87c3972d18f69ee34ca162aa
- **Assessment:**
  - **Verdict:** confirmed
  - **Relationship:** new
  - **Finding id:** unavailable
  - **Explanation:** Required behavior: \`tools\.&lt;index&gt;\.strict\` extra\_body keys must refer to an existing tool list entry; otherwise \`\_extra\_body\` must raise invalid tool index and must not fabricate a dict\-shaped \`tools\` value\. This is shown by the list guard that rejects non\-digit or out\-of\-range indices when target is already a list, and by \`encode\_request\` creating \`body\["tools"\]\` only as a list when \`request\.tools\` is non\-empty\. Actual behavior: with \`request\.tools==\[\]\` body has no \`tools\`, \`strict\_flag\` clears \`protected\` for \`tools\.0\.strict\`, so the intermediate\-creation branch creates \`body\["tools"\]=\{\}\` then \`\{"0":\{\}\}\` then sets \`strict\`, yielding \`\{"0":\{"strict":true\}\}\` dict\. Counterevidence checked: strict boolean\-type check remains but does not validate index; final \`\_extra\_body\` checks cover only \`n\`, \`thinking\`, and budget, with no tools\-shape validation or recovery path; \`PROTECTED\` block is explicitly disabled for this path, so no alternative guard rejects it\. Therefore confirmed\.
  - **Cause:** The \`else\` intermediate branch at lines 109\-112 creates \`\{\}\` for any missing dict key when \`protected\` is False, without validating tool\-index references\. Because \`strict\_flag\` clears \`protected\` for \`tools\.&lt;digit&gt;\.strict\` / \`tools\.&lt;digit&gt;\.function\.strict\`, a missing \`body\["tools"\]\` is fabricated as a dict, and the subsequent \`"0"\` part is then handled as a dict key, so the list\-index guard at lines 93\-95 never applies\.
  - **Trigger:** Configure \`options\.extra\_body\` with \`\{"tools\.0\.strict": true\}\` \(or \`tools\.0\.function\.strict\`\) and call \`encode\_request\` for a request with \`request\.tools==\[\]\`, so \`body\` has no \`tools\` list before \`\_extra\_body\`\.
  - **Consequence:** encode\_request returns a malformed provider body with \`body\["tools"\]==\{"0":\{"strict":\.\.\.\}\}\` dict instead of a list\. It bypasses the intended invalid\-index rejection and is returned to the HTTP sender with no later tools\-shape check, causing downstream provider rejection, wasted inference call, or unexpected payload shape\.
  - **History explanation:** history is empty\. The three prior candidates are distinct causes/locations: gateway deadline reuse, History\.settle missing rowcount, and Session\.analysis post\-cache validation\. None concerns \_extra\_body tool\-index intermediate creation, so relationship is new\.
  - **Contradicts prior:** False
  - **Changed conditions:** No prior established finding for this cause; assessed directly at current revision using supplied llm\_http\.py\. No ownership or recovery\-contract change needed to distinguish it\.
  - **Suggestion supported:** True
  - **Evidence:**
    - **1.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 22
      - **End line:** 26
      - **Quote:**

        ```text
        PROTECTED = {
            "model", "messages", "input", "instructions", "system", "tools", "tool_choice", "stream",
            "api_key", "max_tokens", "max_completion_tokens", "max_output_tokens", "response_format",
            "store", "previous_response_id", "conversation", "background", "authorization", "extra_headers", "include",
        }
        
        ```

    - **2.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 80
      - **End line:** 84
      - **Quote:**

        ```text
            def assign(parts, value):
                dotted = ".".join(parts)
                strict_flag = (len(parts) == 3 and parts[0] == "tools" and parts[1].isdigit() and parts[2] == "strict") or (
                    len(parts) == 4 and parts[0] == "tools" and parts[1].isdigit() and parts[2:] == ["function", "strict"])
                protected = parts[0] in PROTECTED and not strict_flag
        
        ```

    - **3.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 93
      - **End line:** 95
      - **Quote:**

        ```text
                    if isinstance(target, list):
                        if not part.isdigit() or int(part) >= len(target):
                            raise ValueError("extra_body references an invalid tool index")
        
        ```

    - **4.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 109
      - **End line:** 113
      - **Quote:**

        ```text
                        if isinstance(target, dict) and key not in target:
                            if protected:
                                raise ValueError("extra_body cannot replace protected inference inputs, output limits or authentication")
                            target[key] = {}
                        target = target[key]
        
        ```

    - **5.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 181
      - **End line:** 184
      - **Quote:**

        ```text
                if request.tools:
                    body["tools"] = [{"type": "function", "function": {
                        "name": tool.name, "description": tool.description, "parameters": tool.parameters,
                        "strict": _strict_schema(tool.parameters)}} for tool in request.tools]
        
        ```

    - **6.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 261
      - **End line:** 261
      - **Quote:**

        ```text
            return _extra_body(body, connection.get("options", {}).get("extra_body", {}))
        
        ```

- **Status:** open

### 2\. \[medium\] src/mortis\_engine/review/settings\.py:59\-62

- **Content:** RepoSettings\.valid\_rules claims rules\.rules entries require path, rule and merge\_system\_rule fields, but the guard only rejects non\-dicts and extra keys\. An entry such as \{'path': 'x'\} has set\(entry\) \- allowed == empty and therefore passes validation\.
- **Category:** correctness
- **Existing code:**

  ```text
                  raise ValueError(f"rules.{key} must be a list with at most 1000 entries.")
          for entry in value.get("rules", []):
              if (not isinstance(entry, dict)
                      or set(entry) - {"path", "rule", "merge_system_rule"}):
  ```

- **Suggestion code:**

  ```text
  for entry in value.get("rules", []):
      if (not isinstance(entry, dict)
              or set(entry) - {"path", "rule", "merge_system_rule"}
              or {"path", "rule", "merge_system_rule"} - set(entry)):
          raise ValueError("rules.rules entries require path, rule and merge_system_rule fields.")
  ```

- **Trigger:** Validate review configuration with rules=\{'rules': \[\{'path': 'src/'\}\]\} missing rule and merge\_system\_rule via settings\.load or RepoSettings\.model\_validate\.
- **Consequence:** A rule entry missing required fields passes the explicit rules\.rules validation instead of being rejected, so an incomplete configuration is forwarded to planning\.\_rule\_layer rather than raising the stated ValueError\.
- **Evidence:**
  - **1.**
    - **Revision:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Path:** src/mortis\_engine/review/settings\.py
    - **Start line:** 48
    - **End line:** 63
    - **Quote:**

      ```text
      
          @field_validator("rules", mode="before")
          @classmethod
          def valid_rules(cls, value):
              if value is None:
                  return None
              if not isinstance(value, dict) or set(value) - {"rules", "include", "exclude"}:
                  raise ValueError("rules requires a table containing rules, include and exclude lists.")
              for key in ("rules", "include", "exclude"):
                  entries = value.get(key, [])
                  if not isinstance(entries, list) or len(entries) > 1000:
                      raise ValueError(f"rules.{key} must be a list with at most 1000 entries.")
              for entry in value.get("rules", []):
                  if (not isinstance(entry, dict)
                          or set(entry) - {"path", "rule", "merge_system_rule"}):
                      raise ValueError("rules.rules entries require path, rule and merge_system_rule fields.")
      ```

- **Source sha:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
- **Inline line:** 62
- **Id:** finding\-0c134bacbd99e380b9b6c738dec51651
- **Assessment:**
  - **Verdict:** confirmed
  - **Relationship:** new
  - **Finding id:** unavailable
  - **Explanation:** Required behavior per settings\.py message is that rules\.rules entries require path, rule and merge\_system\_rule fields, i\.e\. missing fields must raise ValueError\. Actual behavior: guard only rejects non\-dicts and extra keys via set\(entry\)\-allowed, so \{'path':'x'\} has empty difference and passes, then returns planning\.\_rule\_layer\(value\)\. Counterevidence checked: \_rule\_layer also uses entry\.get with defaults and only validates types of defaulted values, so it normalizes missing fields instead of rejecting; no other guard enforces presence\. Therefore mismatch confirmed\.
  - **Cause:** Guard at valid\_rules only tests not isinstance\(entry, dict\) or set\(entry\) \- allowed, omitting the missing\-keys test allowed \- set\(entry\), so a subset dict yields empty difference and passes\.
  - **Trigger:** Validate rules=\{'rules':\[\{'path':'src/'\}\]\} via RepoSettings\.model\_validate or settings\.load; include/exclude default to \[\] so list checks pass and per\-entry check is reached with subset dict\.
  - **Consequence:** Entry \{'path':'x'\} passes the explicit rules\.rules validation instead of raising the stated ValueError and is forwarded to planning\.\_rule\_layer where \.get defaults normalize it to rule='' and merge\_system\_rule=False\.
  - **History explanation:** history and prior\_candidates are empty; no established finding matches this validator defect, so relationship is new\.
  - **Contradicts prior:** False
  - **Changed conditions:** No prior assessment; current revision retains the extra\-keys\-only check\.
  - **Suggestion supported:** True
  - **Evidence:**
    - **1.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/review/settings\.py
      - **Start line:** 60
      - **End line:** 64
      - **Quote:**

        ```text
                for entry in value.get("rules", []):
                    if (not isinstance(entry, dict)
                            or set(entry) - {"path", "rule", "merge_system_rule"}):
                        raise ValueError("rules.rules entries require path, rule and merge_system_rule fields.")
                return planning._rule_layer(value)
        
        ```

    - **2.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/review/planning\.py
      - **Start line:** 141
      - **End line:** 147
      - **Quote:**

        ```text
            for entry in result["rules"]:
                if (not isinstance(entry, dict) or not isinstance(entry.get("path", ""), str)
                        or not isinstance(entry.get("rule", ""), str)
                        or type(entry.get("merge_system_rule", False)) is not bool):
                    raise ValueError("Invalid project review rule entry.")
                entries.append({"path": entry.get("path", ""), "rule": entry.get("rule", ""),
                                "merge_system_rule": entry.get("merge_system_rule", False)})
        
        ```

- **Status:** open

### 3\. \[medium\] src/mortis\_engine/security\_scans/service\.py:247\-248

- **Content:** Previous\-history IDs are used without per\-record validation: history\_findings\(\) only checks schema\_version and list length, then scan\(\) builds previous\_ids with old\["id"\]\. A history record without "id" passes validation and crashes the comprehension\.
- **Category:** reliability
- **Existing code:**

  ```text
          previous_ids = {old["id"]: old for old in previous_findings}
          by_fingerprint = {old.get("fingerprint"): old for old in previous_findings}
  ```

- **Suggestion code:**

  ```text
  previous_ids = {}
  for old in previous_findings:
      if not isinstance(old, dict) or not isinstance(old.get("id"), str):
          raise ValueError("Previous security finding history contains a record without an id.")
      previous_ids[old["id"]] = old
  ```

- **Trigger:** Call scan\(\) with previous=\{"schema\_version": "mortis\.security/v1", "findings": \[\{\}\]\} or any record missing "id"\. history\_findings\(\) returns it because it only validates that findings is a list\.
- **Consequence:** Unhandled KeyError escapes scan\(\) instead of a ValueError or structured security report\. The snapshot, trivy, analysis, assessment and verification stages are abandoned, no grade/coverage is returned, and the caller cannot distinguish corrupt history from an infrastructure failure\.
- **Evidence:**
  - **1.**
    - **Revision:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Path:** src/mortis\_engine/security\_scans/service\.py
    - **Start line:** 166
    - **End line:** 174
    - **Quote:**

      ```text
      def history_findings(previous):
          if previous is None:
              return []
          if not isinstance(previous, dict) or previous.get("schema_version") != "mortis.security/v1":
              raise ValueError("Previous security history must be a Mortis security report.")
          findings = previous.get("findings", [])
          if not isinstance(findings, list) or len(findings) > 10000:
              raise ValueError("Previous security finding history is invalid or exceeds 10000 records.")
          return json.loads(json.dumps(findings))
      ```

  - **2.**
    - **Revision:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Path:** src/mortis\_engine/security\_scans/service\.py
    - **Start line:** 247
    - **End line:** 248
    - **Quote:**

      ```text
              previous_ids = {old["id"]: old for old in previous_findings}
              by_fingerprint = {old.get("fingerprint"): old for old in previous_findings}
      ```

- **Source sha:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
- **Inline line:** 248
- **Id:** finding\-a96463c8216f065c8ca06146ca8c12b3
- **Assessment:**
  - **Verdict:** confirmed
  - **Relationship:** duplicate
  - **Finding id:** finding\-a96463c8216f065c8ca06146ca8c12b3
  - **Explanation:** Required behavior: invalid previous history should be rejected with ValueError as history\_findings does for bad schema\_version/non\-list/oversize, and scan callers expect entries usable as dicts with id\. Actual behavior: with previous=\{'schema\_version':'mortis\.security/v1','findings':\[\{\}\]\}, history\_findings returns \[\{\}\] via json\.loads\(json\.dumps\(findings\)\), secret\_rules tolerates \{\} via finding\.get, then scan executes previous\_ids=\{old\["id"\]:old for old in previous\_findings\} which raises KeyError for missing id\. Counterevidence checked: no per\-record isinstance/id guard in history\_findings; no guard before previous\_ids; trivy try/except is before this line and its failure path also spreads previous\_findings, so it does not recover this crash\. Therefore confirmed as same defect\.
  - **Cause:** history\_findings\(\) only validates schema\_version and that findings is a list with len&lt;=10000, then returns json round\-trip without per\-record validation; scan\(\) then unconditionally dereferences old\["id"\]\.
  - **Trigger:** Call scan\(\) with previous=\{"schema\_version":"mortis\.security/v1","findings":\[\{\}\]\} or any dict record without string id; accepted by history\_findings then dereferenced at previous\_ids\.
  - **Consequence:** Malformed history with \{\} passes validation then raises unhandled KeyError at previous\_ids construction, aborting scan\(\) outside ValueError validation and outside trivy\-failure structured\-report path, so no grade/coverage/stages are returned\.
  - **History explanation:** Same defect as established history\_1\. History cause, trigger and consequence already cover this: history trigger explicitly includes findings containing dict missing id/fingerprint e\.g\. \{\} fails old\["id"\], and history assessment noted prior isinstance\-only suggestion was incomplete for that reason\. Current proposal narrows to the \{\}\-missing\-id subcase under same ownership \(history\_findings validation\) and same recovery contract \(ValueError vs structured report\)\. Different exception type/line focus does not establish distinct cause\. Prior candidate candidate\-a96463c8216f065c8ca06146ca8c12b3 is a proposal ID, not usable as finding\_id\.
  - **Contradicts prior:** False
  - **Changed conditions:** No relevant code change; the same validation gap assessed in history persists at current revision\. File is newly added in diff but logic for history\_findings and previous\_ids is identical\.
  - **Suggestion supported:** True
  - **Evidence:**
    - **1.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/security\_scans/service\.py
      - **Start line:** 166
      - **End line:** 174
      - **Quote:**

        ```text
        def history_findings(previous):
            if previous is None:
                return []
            if not isinstance(previous, dict) or previous.get("schema_version") != "mortis.security/v1":
                raise ValueError("Previous security history must be a Mortis security report.")
            findings = previous.get("findings", [])
            if not isinstance(findings, list) or len(findings) > 10000:
                raise ValueError("Previous security finding history is invalid or exceeds 10000 records.")
            return json.loads(json.dumps(findings))
        
        ```

    - **2.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/security\_scans/service\.py
      - **Start line:** 177
      - **End line:** 183
      - **Quote:**

        ```text
        def secret_rules(report):
            rules = []
            for finding in history_findings(report):
                evidence = finding["evidence"] if isinstance(finding.get("evidence"), dict) else finding
                if evidence.get("kind") == "secret":
                    rules.append({key: evidence[key] for key in ("rule_id", "match_bytes", "identity")})
            return rules
        
        ```

    - **3.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/security\_scans/service\.py
      - **Start line:** 247
      - **End line:** 248
      - **Quote:**

        ```text
                previous_ids = {old["id"]: old for old in previous_findings}
                by_fingerprint = {old.get("fingerprint"): old for old in previous_findings}
        
        ```

- **Status:** open

### 4\. \[medium\] src/mortis\_engine/llm\_http\.py:353

- **Content:** OpenAI Chat decoding iterates message\.get\("tool\_calls", \[\]\) directly\. That default only covers a missing key\. When a provider returns explicit JSON null \("tool\_calls": null, common for OpenAI\-compatible endpoints when no tools are called\), message\.get returns None and iteration raises TypeError, which is converted to malformed\_response\. Nearby optional fields \(content, reasoning\_content/details\) explicitly allow None, but tool\_calls does not\.
- **Category:** Correctness
- **Existing code:**

  ```text
              for call in message.get("tool_calls", []):
  ```

- **Suggestion code:**

  ```text
  for call in message.get("tool_calls") or []:
  ```

- **Trigger:** Provider returns choices\[0\]\.message as \{"content": "hi", "tool\_calls": None\} with finish\_reason "stop" for protocol "openai"\.
- **Consequence:** A valid Chat Completions response with explicit null tool\_calls is rejected as malformed\_response\. The caller receives InferenceError malformed\_response instead of a usable Response, wasting the request, token budget and retry/time budget and potentially failing review/scan stages\.
- **Evidence:**
  - **1.**
    - **Revision:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Path:** src/mortis\_engine/llm\_http\.py
    - **Start line:** 348
    - **End line:** 353
    - **Quote:**

      ```text
                  content = message.get("content")
                  if content is not None:
                      if not isinstance(content, str):
                          raise ValueError()
                      texts.append(content)
                  for call in message.get("tool_calls", []):
      ```

- **Source sha:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
- **Inline line:** 353
- **Id:** finding\-fa5c7889c071ef7d4eed7c77791b18b6
- **Assessment:**
  - **Verdict:** confirmed
  - **Relationship:** new
  - **Finding id:** unavailable
  - **Explanation:** Required behavior: explicit null for optional Chat message fields means absent\. This is shown by content handling that skips when None and reasoning\_content/details handling that explicitly allows None, and by stop=="stop" being defined as complete so empty calls with content should decode to Response\. Actual behavior: \`for call in message\.get\("tool\_calls", \[\]\):\` returns None when wire value is null, \`for \.\.\. in None\` raises TypeError, which the broad handler converts to malformed\_response\. Counterevidence checked: no isinstance/None guard for tool\_calls, no alternative recovery; wire dict flows unfiltered from post\_json to decode\_response via send\. Therefore confirmed\.
  - **Cause:** OpenAI Chat branch iterates \`message\.get\("tool\_calls", \[\]\)\` directly\. The default only applies when the key is missing; explicit JSON null returns None, which is not iterable\. No None\-guard or isinstance check precedes iteration, unlike nearby optional fields\.
  - **Trigger:** Provider returns choices\[0\]\.message as \{"content":"hi","tool\_calls":None\} with finish\_reason "stop" for protocol "openai"; wire JSON null survives post\_json and reaches decode\_response as None\.
  - **Consequence:** A valid Chat Completions response with explicit \`"tool\_calls": null\` and \`finish\_reason "stop"\` raises TypeError inside the try and is converted to InferenceError malformed\_response instead of returning Response with empty tool\_calls\. Caller loses a usable response and wastes request/budget\.
  - **History explanation:** history\_1 concerns \_extra\_body intermediate creation fabricating dict\-shaped body\["tools"\] at lines 109\-113, a different cause, location, trigger and fix\. No established finding concerns OpenAI decode tool\_calls null handling, so relationship is new\.
  - **Contradicts prior:** False
  - **Changed conditions:** No prior established finding for this cause; assessed directly at current revision using supplied llm\_http\.py\. No ownership or recovery\-contract change needed to distinguish it\.
  - **Suggestion supported:** True
  - **Evidence:**
    - **1.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 348
      - **End line:** 353
      - **Quote:**

        ```text
                    content = message.get("content")
                    if content is not None:
                        if not isinstance(content, str):
                            raise ValueError()
                        texts.append(content)
                    for call in message.get("tool_calls", []):
        
        ```

    - **2.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 358
      - **End line:** 362
      - **Quote:**

        ```text
                    for key in ("reasoning_content", "reasoning_details"):
                        if key in message and message[key] is not None:
                            if not isinstance(message[key], str if key == "reasoning_content" else list):
                                raise ValueError()
                            provider_data[key] = copy.deepcopy(message[key])
        
        ```

    - **3.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 363
      - **End line:** 363
      - **Quote:**

        ```text
                    complete, tool_stop = {"stop"}, "tool_calls"
        
        ```

    - **4.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 342
      - **End line:** 343
      - **Quote:**

        ```text
                    choice, = choices
                    message, stop = choice["message"], choice["finish_reason"]
        
        ```

    - **5.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 434
      - **End line:** 437
      - **Quote:**

        ```text
            except InferenceError:
                raise
            except (KeyError, TypeError, ValueError, AttributeError, ValidationError, RecursionError) as exc:
                raise _invalid(**common) from exc
        
        ```

    - **6.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/llm\_http\.py
      - **Start line:** 609
      - **End line:** 611
      - **Quote:**

        ```text
            data, identity = await post_json(client, base + suffix, headers, body, stopped=stopped,
                                             timeout=duration - (time.monotonic() - started), retry_codes=options.get("retry_codes"))
            return decode_response(protocol, data, request, identity)
        
        ```

- **Status:** open

### 5\. \[medium\] src/mortis\_engine/review/settings\.py:130\-132

- **Content:** load\_scan computes model with \`scan\.pop\("model", ""\) or shared\.model\` before type\-checking\. Any falsy non\-string therefore falls back to shared\.model, making the subsequent isinstance check unable to reject it\.
- **Category:** correctness
- **Existing code:**

  ```text
      scan = dict(scan)
      model = scan.pop("model", "") or shared.model
      if not isinstance(model, str) or len(model) > 150:
  ```

- **Suggestion code:**

  ```text
  raw = scan.pop("model", "")
  model = shared.model if raw == "" else raw
  if not isinstance(model, str) or len(model) > 150:
      raise ValueError("Scan model must be a model ID of at most 150 characters.")
  ```

- **Trigger:** Provide a scan table with a falsy non\-string model, for example mortis\.toml \[scan\] model = 0, then call load\_scan\.
- **Consequence:** An invalid non\-string falsy scan model such as 0, False or None is silently replaced by the shared review model instead of being rejected, so a misconfigured \[scan\] model proceeds with an unintended model\.
- **Evidence:**
  - **1.**
    - **Revision:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Path:** src/mortis\_engine/review/settings\.py
    - **Start line:** 124
    - **End line:** 135
    - **Quote:**

      ```text
      
      def load_scan(repo, env, sha):
          from mortis_engine.security_scans.service import ScanOptions
      
          review, scan = _tables(repo, env, sha)
          shared = RepoSettings.model_validate(review)
          scan = dict(scan)
          model = scan.pop("model", "") or shared.model
          if not isinstance(model, str) or len(model) > 150:
              raise ValueError("Scan model must be a model ID of at most 150 characters.")
          return {**ScanOptions.model_validate({"language": shared.language, "exclusions": shared.exclusions,
                                               "max_output_tokens": shared.max_output_tokens,
      ```

- **Source sha:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
- **Inline line:** 132
- **Id:** finding\-060d59c5f191b4765eed70d2ec47e8ab
- **Assessment:**
  - **Verdict:** confirmed
  - **Relationship:** new
  - **Finding id:** unavailable
  - **Explanation:** Required behavior: per the ValueError message "Scan model must be a model ID of at most 150 characters" and the truthy path, a non\-string scan model must be rejected\. Actual behavior: \`scan\.pop\("model", ""\) or shared\.model\` substitutes shared\.model for any falsy scan value, so 0/False/None bypass the \`isinstance\(model,str\)\` check\. Counterevidence checked: shared\.model is declared \`str\`, ScanOptions is validated after \`pop\` with \`\*\*scan\` so it does not see model, and no other guard validates scan model type; \`\.strip\(\)\` on return assumes str\. Therefore mismatch confirmed\.
  - **Cause:** Line 131 computes \`scan\.pop\("model", ""\) or shared\.model\`; Python \`or\` replaces any falsy value \(0, False, None, \[\], \{\}\) with shared\.model before the type check, so falsy non\-strings never reach \`isinstance\` validation\.
  - **Trigger:** Configure mortis\.toml with \[scan\] model = 0 \(or false\) so \_tables returns scan=\{"model":0\}, then call load\_scan; 0 is falsy so \`0 or shared\.model\` yields shared\.model\.
  - **Consequence:** A misconfigured falsy non\-string scan model such as \`model = 0\` or \`model = false\` raises no ValueError and silently proceeds using the shared review model instead of being rejected\.
  - **History explanation:** history\_1 concerns RepoSettings\.valid\_rules only rejecting extra keys and missing required path/rule/merge\_system\_rule fields at lines 60\-63\. This proposal concerns load\_scan scan\-model fallback at lines 130\-132 with different function, cause, trigger and consequence, so it is distinct and relationship is new\.
  - **Contradicts prior:** False
  - **Changed conditions:** No prior assessment for this load\_scan defect; current revision retains the \`or shared\.model\` fallback before isinstance check\.
  - **Suggestion supported:** True
  - **Evidence:**
    - **1.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/review/settings\.py
      - **Start line:** 130
      - **End line:** 133
      - **Quote:**

        ```text
            scan = dict(scan)
            model = scan.pop("model", "") or shared.model
            if not isinstance(model, str) or len(model) > 150:
                raise ValueError("Scan model must be a model ID of at most 150 characters.")
        
        ```

    - **2.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/review/settings\.py
      - **Start line:** 36
      - **End line:** 36
      - **Quote:**

        ```text
            model: str = Field(default="", max_length=150)
        
        ```

    - **3.**
      - **Revision:** current
      - **Path:** src/mortis\_engine/review/settings\.py
      - **Start line:** 134
      - **End line:** 136
      - **Quote:**

        ```text
            return {**ScanOptions.model_validate({"language": shared.language, "exclusions": shared.exclusions,
                                                 "max_output_tokens": shared.max_output_tokens,
                                                 **scan}).model_dump(), "model": model.strip()}
        
        ```

- **Status:** open

## Coverage

- **Selected files:** 76
- **Completed files:** 53
- **Failed files:** 23
- **Reused files:** 0
- **Waived files:** 0
- **Failed:**
  - **1.**
    - **Path:** src/mortis\_engine/llm\_http\.py
  - **2.**
    - **Path:** src/mortis\_engine/operations\.py
  - **3.**
    - **Path:** src/mortis\_engine/providers\.py
  - **4.**
    - **Path:** src/mortis\_engine/review/policy\.py
  - **5.**
    - **Path:** src/mortis\_engine/review/snapshots\.py
  - **6.**
    - **Path:** src/mortis\_engine/review/source\.py
  - **7.**
    - **Path:** src/mortis\_engine/review/summary\.py
  - **8.**
    - **Path:** src/mortis\_engine/review/verification\.py
  - **9.**
    - **Path:** src/mortis\_engine/review/workflow\_contracts\.py
  - **10.**
    - **Path:** src/mortis\_engine/review/workflows\.py
  - **11.**
    - **Path:** src/mortis\_engine/security\_scans/engine\.py
  - **12.**
    - **Path:** src/mortis\_engine/security\_scans/inference\.py
  - **13.**
    - **Path:** src/mortis\_engine/security\_scans/payload\.py
  - **14.**
    - **Path:** src/mortis\_engine/security\_scans/policy\.py
  - **15.**
    - **Path:** src/mortis\_engine/security\_scans/sandbox\.py
  - **16.**
    - **Path:** src/mortis\_engine/security\_scans/scanner\.py
  - **17.**
    - **Path:** src/mortis\_engine/security\_scans/service\.py
  - **18.**
    - **Path:** src/mortis\_engine/workflow\.py
  - **19.**
    - **Path:** tests/code\_graph/\_\_init\_\_\.py
  - **20.**
    - **Path:** tests/review/\_\_init\_\_\.py
  - **21.**
    - **Path:** tests/review/conftest\.py
  - **22.**
    - **Path:** tests/review/fixtures/partial\_review\.json
  - **23.**
    - **Path:** tests/review/git\_helpers\.py
- **Waived:** none
- **Units:**
  - **Completed:** 64
  - **Failed:** 28
- **Incomplete units:**
  - **1.**
    - **Path:** src/mortis\_engine/llm\_http\.py
    - **Start line:** 517
    - **End line:** 611
    - **Status:** failed
    - **Error:** usage\_unknown
  - **2.**
    - **Path:** src/mortis\_engine/operations\.py
    - **Start line:** 1
    - **End line:** 125
    - **Status:** failed
    - **Error:** usage\_unknown
  - **3.**
    - **Path:** src/mortis\_engine/providers\.py
    - **Start line:** 1
    - **End line:** 68
    - **Status:** failed
    - **Error:** usage\_unknown
  - **4.**
    - **Path:** src/mortis\_engine/review/policy\.py
    - **Start line:** 1
    - **End line:** 31
    - **Status:** failed
    - **Error:** usage\_unknown
  - **5.**
    - **Path:** src/mortis\_engine/review/snapshots\.py
    - **Start line:** 1
    - **End line:** 235
    - **Status:** failed
    - **Error:** usage\_unknown
  - **6.**
    - **Path:** src/mortis\_engine/review/source\.py
    - **Start line:** 1
    - **End line:** 267
    - **Status:** failed
    - **Error:** usage\_unknown
  - **7.**
    - **Path:** src/mortis\_engine/review/source\.py
    - **Start line:** 268
    - **End line:** 273
    - **Status:** failed
    - **Error:** usage\_unknown
  - **8.**
    - **Path:** src/mortis\_engine/review/summary\.py
    - **Start line:** 1
    - **End line:** 141
    - **Status:** failed
    - **Error:** provider\_http\_503
  - **9.**
    - **Path:** src/mortis\_engine/review/verification\.py
    - **Start line:** 1
    - **End line:** 240
    - **Status:** failed
    - **Error:** usage\_unknown
  - **10.**
    - **Path:** src/mortis\_engine/review/verification\.py
    - **Start line:** 241
    - **End line:** 263
    - **Status:** failed
    - **Error:** usage\_unknown
  - **11.**
    - **Path:** src/mortis\_engine/review/workflow\_contracts\.py
    - **Start line:** 1
    - **End line:** 70
    - **Status:** failed
    - **Error:** usage\_unknown
  - **12.**
    - **Path:** src/mortis\_engine/review/workflows\.py
    - **Start line:** 1
    - **End line:** 245
    - **Status:** failed
    - **Error:** usage\_unknown
  - **13.**
    - **Path:** src/mortis\_engine/review/workflows\.py
    - **Start line:** 246
    - **End line:** 325
    - **Status:** failed
    - **Error:** usage\_unknown
  - **14.**
    - **Path:** src/mortis\_engine/security\_scans/engine\.py
    - **Start line:** 246
    - **End line:** 482
    - **Status:** failed
    - **Error:** usage\_unknown
  - **15.**
    - **Path:** src/mortis\_engine/security\_scans/engine\.py
    - **Start line:** 483
    - **End line:** 505
    - **Status:** failed
    - **Error:** usage\_unknown
  - **16.**
    - **Path:** src/mortis\_engine/security\_scans/inference\.py
    - **Start line:** 1
    - **End line:** 133
    - **Status:** failed
    - **Error:** usage\_unknown
  - **17.**
    - **Path:** src/mortis\_engine/security\_scans/payload\.py
    - **Start line:** 1
    - **End line:** 78
    - **Status:** failed
    - **Error:** usage\_unknown
  - **18.**
    - **Path:** src/mortis\_engine/security\_scans/policy\.py
    - **Start line:** 1
    - **End line:** 39
    - **Status:** failed
    - **Error:** usage\_unknown
  - **19.**
    - **Path:** src/mortis\_engine/security\_scans/sandbox\.py
    - **Start line:** 1
    - **End line:** 36
    - **Status:** failed
    - **Error:** usage\_unknown
  - **20.**
    - **Path:** src/mortis\_engine/security\_scans/scanner\.py
    - **Start line:** 1
    - **End line:** 169
    - **Status:** failed
    - **Error:** usage\_unknown
  - **21.**
    - **Path:** src/mortis\_engine/security\_scans/service\.py
    - **Start line:** 258
    - **End line:** 422
    - **Status:** failed
    - **Error:** usage\_unknown
  - **22.**
    - **Path:** src/mortis\_engine/workflow\.py
    - **Start line:** 1
    - **End line:** 259
    - **Status:** failed
    - **Error:** usage\_unknown
  - **23.**
    - **Path:** src/mortis\_engine/workflow\.py
    - **Start line:** 260
    - **End line:** 269
    - **Status:** failed
    - **Error:** usage\_unknown
  - **24.**
    - **Path:** tests/code\_graph/\_\_init\_\_\.py
    - **Start line:** 1
    - **End line:** 1
    - **Status:** failed
    - **Error:** usage\_unknown
  - **25.**
    - **Path:** tests/review/\_\_init\_\_\.py
    - **Start line:** 1
    - **End line:** 1
    - **Status:** failed
    - **Error:** usage\_unknown
  - **26.**
    - **Path:** tests/review/conftest\.py
    - **Start line:** 1
    - **End line:** 9
    - **Status:** failed
    - **Error:** usage\_unknown
  - **27.**
    - **Path:** tests/review/fixtures/partial\_review\.json
    - **Start line:** 1
    - **End line:** 90
    - **Status:** failed
    - **Error:** usage\_unknown
  - **28.**
    - **Path:** tests/review/git\_helpers\.py
    - **Start line:** 1
    - **End line:** 18
    - **Status:** failed
    - **Error:** usage\_unknown

## Excluded files

- **1.**
  - **Path:** \.codex/agents/contract\_reviewer\.toml
  - **Old path:** unavailable
  - **Reason:** excluded
- **2.**
  - **Path:** \.codex/agents/explorer\.toml
  - **Old path:** unavailable
  - **Reason:** excluded
- **3.**
  - **Path:** \.codex/agents/implementer\.toml
  - **Old path:** unavailable
  - **Reason:** excluded
- **4.**
  - **Path:** \.codex/agents/security\_reviewer\.toml
  - **Old path:** unavailable
  - **Reason:** excluded
- **5.**
  - **Path:** \.codex/agents/verification\_runner\.toml
  - **Old path:** unavailable
  - **Reason:** excluded
- **6.**
  - **Path:** \.codex/agents/workflow\_reviewer\.toml
  - **Old path:** unavailable
  - **Reason:** excluded
- **7.**
  - **Path:** \.codex/config\.toml
  - **Old path:** unavailable
  - **Reason:** excluded
- **8.**
  - **Path:** AGENTS\.md
  - **Old path:** unavailable
  - **Reason:** excluded
- **9.**
  - **Path:** tests/code\_graph/test\_graph\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **10.**
  - **Path:** tests/review/test\_command\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **11.**
  - **Path:** tests/review/test\_execution\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **12.**
  - **Path:** tests/review/test\_planning\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **13.**
  - **Path:** tests/review/test\_recorded\_review\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **14.**
  - **Path:** tests/review/test\_snapshots\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **15.**
  - **Path:** tests/review/test\_workflows\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **16.**
  - **Path:** tests/security\_scans/test\_security\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **17.**
  - **Path:** tests/test\_admission\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **18.**
  - **Path:** tests/test\_assessments\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **19.**
  - **Path:** tests/test\_cli\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **20.**
  - **Path:** tests/test\_discovery\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **21.**
  - **Path:** tests/test\_findings\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **22.**
  - **Path:** tests/test\_graph\_limits\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **23.**
  - **Path:** tests/test\_limits\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **24.**
  - **Path:** tests/test\_llm\_bedrock\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **25.**
  - **Path:** tests/test\_llm\_http\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **26.**
  - **Path:** tests/test\_local\_runtime\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **27.**
  - **Path:** tests/test\_probe\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **28.**
  - **Path:** tests/test\_report\_cli\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **29.**
  - **Path:** tests/test\_reporting\.py
  - **Old path:** unavailable
  - **Reason:** excluded
- **30.**
  - **Path:** uv\.lock
  - **Old path:** unavailable
  - **Reason:** excluded

## Retained findings

- **1.**
  - **Id:** finding\-d14fe12459da48c94c43a7bfb38a7977
  - **Status:** open
  - **Observation:** not\_observed
  - **Sha:** f85331a2871ce2b9b01a92157144110de424ee26
  - **Path:** src/mortis\_engine/code\_graph/extract\.py
  - **Start line:** 231
  - **End line:** 239
  - **Severity:** medium
  - **Content:** main\(\) request\-load block only catches ValueError while request\_path\.stat\(\) and request\_path\.read\_bytes\(\) raise OSError on missing/unreadable files\. The OSError escapes the first try/except instead of returning the defined error exit code\.
- **2.**
  - **Id:** finding\-678431bfd74b4df7719c60595c2bbb1a
  - **Status:** open
  - **Observation:** not\_observed
  - **Sha:** f85331a2871ce2b9b01a92157144110de424ee26
  - **Path:** src/mortis\_engine/code\_graph/transport\.py
  - **Start line:** 96
  - **End line:** 103
  - **Severity:** medium
  - **Content:** Cleanup Docker removal in the extraction finally block is unguarded and can mask the original result\. subprocess\.run with timeout=15 can raise TimeoutExpired or OSError if the daemon hangs or the client fails, replacing the pending TimeoutError, ReviewStopped, or ValueError from the wait loop and validation path\.
- **3.**
  - **Id:** finding\-c29c040e6b6de35133cfa9332a6da1a1
  - **Status:** open
  - **Observation:** not\_observed
  - **Sha:** f85331a2871ce2b9b01a92157144110de424ee26
  - **Path:** src/mortis\_engine/code\_graph/validation\.py
  - **Start line:** 54
  - **End line:** 56
  - **Severity:** medium
  - **Content:** Symbol/import file\_path is used as a direct dictionary key without verifying the referenced file was admitted as parsed content\. Files with status excluded, unsupported, or failed never populate sources/lines/languages, as do unknown paths, so any fact referencing them raises KeyError instead of ValueError\.
- **4.**
  - **Id:** finding\-3dca3c9d92f6df8e8634e11542d02b7a
  - **Status:** open
  - **Observation:** not\_observed
  - **Sha:** f85331a2871ce2b9b01a92157144110de424ee26
  - **Path:** src/mortis\_engine/review/snapshots\.py
  - **Start line:** 86
  - **End line:** 101
  - **Severity:** medium
  - **Content:** Overbroad OSError handler in \_worktree\_stat aborts snapshot on ENOTDIR\. Replacing a staged directory with a regular file makes traversal raise NotADirectoryError, which is misreported as symlink traversal instead of missing path\.
- **5.**
  - **Id:** finding\-7102e05508ca06a01c79c6c69f2a3498
  - **Status:** open
  - **Observation:** not\_observed
  - **Sha:** f85331a2871ce2b9b01a92157144110de424ee26
  - **Path:** src/mortis\_engine/review/snapshots\.py
  - **Start line:** 105
  - **End line:** 135
  - **Severity:** medium
  - **Content:** Overbroad OSError handler in \_worktree\_bytes aborts snapshot on ENOTDIR\. A parent directory replaced by a regular file raises NotADirectoryError during component open/stat, which should return None for deleted content\.
- **6.**
  - **Id:** finding\-4bbf2d243097a1408615193bb042d857
  - **Status:** open
  - **Observation:** not\_observed
  - **Sha:** f85331a2871ce2b9b01a92157144110de424ee26
  - **Path:** src/mortis\_engine/review/source\.py
  - **Start line:** 77
  - **End line:** 79
  - **Severity:** medium
  - **Content:** read\_source treats U\+FFFD as proof of invalid UTF\-8, but git\(\) decodes with errors='replace'\. A file that validly contains the replacement character \(UTF\-8 bytes EF BF BD\) therefore decodes to U\+FFFD and is misclassified as binary/non\-UTF\-8\.
- **7.**
  - **Id:** finding\-a96463c8216f065c8ca06146ca8c12b3
  - **Status:** open
  - **Observation:** observed
  - **Sha:** f85331a2871ce2b9b01a92157144110de424ee26
  - **Path:** src/mortis\_engine/security\_scans/service\.py
  - **Start line:** 169
  - **End line:** 177
  - **Severity:** medium
  - **Content:** history\_findings\(\) validates only that previous is a dict with the expected schema\_version and that findings is a list under 10000 entries, but does not validate that each entry is a dict\. Downstream code unconditionally dereferences entries as dicts, so a malformed history crashes the scan\.

## Candidate assessments

- **1.**
  - **Id:** candidate\-3d778a2b87c3972d18f69ee34ca162aa
  - **Status:** completed
  - **Path:** src/mortis\_engine/llm\_http\.py
  - **Start line:** 109
  - **End line:** 113
  - **Verdict:** confirmed
  - **Finding id:** unavailable
  - **Relationship:** new
  - **Explanation:** Required behavior: \`tools\.&lt;index&gt;\.strict\` extra\_body keys must refer to an existing tool list entry; otherwise \`\_extra\_body\` must raise invalid tool index and must not fabricate a dict\-shaped \`tools\` value\. This is shown by the list guard that rejects non\-digit or out\-of\-range indices when target is already a list, and by \`encode\_request\` creating \`body\["tools"\]\` only as a list when \`request\.tools\` is non\-empty\. Actual behavior: with \`request\.tools==\[\]\` body has no \`tools\`, \`strict\_flag\` clears \`protected\` for \`tools\.0\.strict\`, so the intermediate\-creation branch creates \`body\["tools"\]=\{\}\` then \`\{"0":\{\}\}\` then sets \`strict\`, yielding \`\{"0":\{"strict":true\}\}\` dict\. Counterevidence checked: strict boolean\-type check remains but does not validate index; final \`\_extra\_body\` checks cover only \`n\`, \`thinking\`, and budget, with no tools\-shape validation or recovery path; \`PROTECTED\` block is explicitly disabled for this path, so no alternative guard rejects it\. Therefore confirmed\.
- **2.**
  - **Id:** candidate\-fa5c7889c071ef7d4eed7c77791b18b6
  - **Status:** completed
  - **Path:** src/mortis\_engine/llm\_http\.py
  - **Start line:** 353
  - **End line:** 353
  - **Verdict:** confirmed
  - **Finding id:** unavailable
  - **Relationship:** new
  - **Explanation:** Required behavior: explicit null for optional Chat message fields means absent\. This is shown by content handling that skips when None and reasoning\_content/details handling that explicitly allows None, and by stop=="stop" being defined as complete so empty calls with content should decode to Response\. Actual behavior: \`for call in message\.get\("tool\_calls", \[\]\):\` returns None when wire value is null, \`for \.\.\. in None\` raises TypeError, which the broad handler converts to malformed\_response\. Counterevidence checked: no isinstance/None guard for tool\_calls, no alternative recovery; wire dict flows unfiltered from post\_json to decode\_response via send\. Therefore confirmed\.
- **3.**
  - **Id:** candidate\-0c134bacbd99e380b9b6c738dec51651
  - **Status:** completed
  - **Path:** src/mortis\_engine/review/settings\.py
  - **Start line:** 59
  - **End line:** 62
  - **Verdict:** confirmed
  - **Finding id:** unavailable
  - **Relationship:** new
  - **Explanation:** Required behavior per settings\.py message is that rules\.rules entries require path, rule and merge\_system\_rule fields, i\.e\. missing fields must raise ValueError\. Actual behavior: guard only rejects non\-dicts and extra keys via set\(entry\)\-allowed, so \{'path':'x'\} has empty difference and passes, then returns planning\.\_rule\_layer\(value\)\. Counterevidence checked: \_rule\_layer also uses entry\.get with defaults and only validates types of defaulted values, so it normalizes missing fields instead of rejecting; no other guard enforces presence\. Therefore mismatch confirmed\.
- **4.**
  - **Id:** candidate\-060d59c5f191b4765eed70d2ec47e8ab
  - **Status:** completed
  - **Path:** src/mortis\_engine/review/settings\.py
  - **Start line:** 130
  - **End line:** 132
  - **Verdict:** confirmed
  - **Finding id:** unavailable
  - **Relationship:** new
  - **Explanation:** Required behavior: per the ValueError message "Scan model must be a model ID of at most 150 characters" and the truthy path, a non\-string scan model must be rejected\. Actual behavior: \`scan\.pop\("model", ""\) or shared\.model\` substitutes shared\.model for any falsy scan value, so 0/False/None bypass the \`isinstance\(model,str\)\` check\. Counterevidence checked: shared\.model is declared \`str\`, ScanOptions is validated after \`pop\` with \`\*\*scan\` so it does not see model, and no other guard validates scan model type; \`\.strip\(\)\` on return assumes str\. Therefore mismatch confirmed\.
- **5.**
  - **Id:** candidate\-1d282d79d4706479a3540d7827d36432
  - **Status:** completed
  - **Path:** src/mortis\_engine/security\_scans/service\.py
  - **Start line:** 247
  - **End line:** 248
  - **Verdict:** confirmed
  - **Finding id:** finding\-a96463c8216f065c8ca06146ca8c12b3
  - **Relationship:** duplicate
  - **Explanation:** Required behavior: invalid previous history should be rejected with ValueError as history\_findings does for bad schema\_version/non\-list/oversize, and scan callers expect entries usable as dicts with id\. Actual behavior: with previous=\{'schema\_version':'mortis\.security/v1','findings':\[\{\}\]\}, history\_findings returns \[\{\}\] via json\.loads\(json\.dumps\(findings\)\), secret\_rules tolerates \{\} via finding\.get, then scan executes previous\_ids=\{old\["id"\]:old for old in previous\_findings\} which raises KeyError for missing id\. Counterevidence checked: no per\-record isinstance/id guard in history\_findings; no guard before previous\_ids; trivy try/except is before this line and its failure path also spreads previous\_findings, so it does not recover this crash\. Therefore confirmed as same defect\.
- **6.**
  - **Id:** candidate\-693a5d5158ec83df1f52d431529dc900
  - **Status:** completed
  - **Path:** src/mortis\_engine/security\_scans/service\.py
  - **Start line:** 177
  - **End line:** 183
  - **Verdict:** confirmed
  - **Finding id:** finding\-a96463c8216f065c8ca06146ca8c12b3
  - **Relationship:** duplicate
  - **Explanation:** Required behavior: invalid previous history should raise ValueError as history\_findings does for bad schema\_version/non\-list/oversize, and scan callers expect validated entries\. Actual behavior: with previous=\{'schema\_version':'mortis\.security/v1','findings':\[\{'kind':'secret','rule\_id':'x'\}\]\}, history\_findings returns the list via json round\-trip, secret\_rules takes evidence=finding then evidence\.get\('kind'\)=='secret' true and evaluates evidence\['match\_bytes'\]/evidence\['identity'\] raising KeyError\. The first crash is at Snapshot creation key=identity\_key\(create=not \(previous and secret\_rules\(previous\)\)\) which is before the trivy try/except, so no structured\-report recovery\. Counterevidence checked: no per\-entry isinstance/presence guard in history\_findings; no presence/type guard in secret\_rules; trivy try starts later and its failure path itself spreads previous\_findings so does not recover this path\. Therefore confirmed as same validation\-gap defect\.
- **7.**
  - **Id:** candidate\-6861c63399b416efc0b12762556c5ef6
  - **Status:** completed
  - **Path:** src/mortis\_engine/security\_scans/service\.py
  - **Start line:** 243
  - **End line:** 250
  - **Verdict:** rejected
  - **Finding id:** unavailable
  - **Relationship:** new
  - **Explanation:** Required behavior: scan\(\) must handle malformed scanner output only if a supplied contract or demonstrated caller defines custom\-scanner output as untrusted and requires a structured failed/partial report instead of an exception\. No supplied contract defines that: scan\(scanner=\.\.\.\) has no documented return\-shape validation, and trivy\(\) is the documented producer\. Actual behavior: selected\_scanner call is inside try/except that converts scanner exceptions to a failed report, while result\["problems"\] and finding\["fingerprint"\] are dereferenced outside that try\. With a custom scanner returning \{"findings": \[\{"path": "a\.py"\}\], "problems": \[\]\} this would raise KeyError\. Counterevidence: scanner\.py validates raw Trivy JSON and converts ValueError/KeyError/TypeError/AttributeError/IndexError to ValueError\("Trivy returned an unsupported security report\.\.\."\), which the service try/except then converts to a failed report; normalize\_trivy returns \{"findings":\.\.\.,"targets":\.\.\.,"problems":\.\.\.\}\. The proposal therefore requires the producer \(custom scanner\) to emit malformed data, but provides no caller or contract proving such arbitrary returns are a supported case versus caller misuse, and the default producer cannot emit it\. This is speculative hardening/preference, not a concrete contract mismatch\. Therefore rejected\. Suggestion raises ValueError outside the trivy\-failure path so still produces no report, so incomplete for its own stated consequence\.

## Errors

- **1.**
  - **Path:** src/mortis\_engine/llm\_http\.py
  - **Message:** usage\_unknown
- **2.**
  - **Path:** src/mortis\_engine/operations\.py
  - **Message:** usage\_unknown
- **3.**
  - **Path:** src/mortis\_engine/providers\.py
  - **Message:** usage\_unknown
- **4.**
  - **Path:** src/mortis\_engine/review/policy\.py
  - **Message:** usage\_unknown
- **5.**
  - **Path:** src/mortis\_engine/review/snapshots\.py
  - **Message:** usage\_unknown
- **6.**
  - **Path:** src/mortis\_engine/review/source\.py
  - **Message:** usage\_unknown
- **7.**
  - **Path:** src/mortis\_engine/review/summary\.py
  - **Message:** provider\_http\_503
- **8.**
  - **Path:** src/mortis\_engine/review/verification\.py
  - **Message:** usage\_unknown
- **9.**
  - **Path:** src/mortis\_engine/review/workflow\_contracts\.py
  - **Message:** usage\_unknown
- **10.**
  - **Path:** src/mortis\_engine/review/workflows\.py
  - **Message:** usage\_unknown
- **11.**
  - **Path:** src/mortis\_engine/security\_scans/engine\.py
  - **Message:** usage\_unknown
- **12.**
  - **Path:** src/mortis\_engine/security\_scans/inference\.py
  - **Message:** usage\_unknown
- **13.**
  - **Path:** src/mortis\_engine/security\_scans/payload\.py
  - **Message:** usage\_unknown
- **14.**
  - **Path:** src/mortis\_engine/security\_scans/policy\.py
  - **Message:** usage\_unknown
- **15.**
  - **Path:** src/mortis\_engine/security\_scans/sandbox\.py
  - **Message:** usage\_unknown
- **16.**
  - **Path:** src/mortis\_engine/security\_scans/scanner\.py
  - **Message:** usage\_unknown
- **17.**
  - **Path:** src/mortis\_engine/security\_scans/service\.py
  - **Message:** usage\_unknown
- **18.**
  - **Path:** src/mortis\_engine/workflow\.py
  - **Message:** usage\_unknown
- **19.**
  - **Path:** tests/code\_graph/\_\_init\_\_\.py
  - **Message:** usage\_unknown
- **20.**
  - **Path:** tests/review/\_\_init\_\_\.py
  - **Message:** usage\_unknown
- **21.**
  - **Path:** tests/review/conftest\.py
  - **Message:** usage\_unknown
- **22.**
  - **Path:** tests/review/fixtures/partial\_review\.json
  - **Message:** usage\_unknown
- **23.**
  - **Path:** tests/review/git\_helpers\.py
  - **Message:** usage\_unknown

## Warnings

None recorded.

## Stage errors

None recorded.

## Token usage

- **Total tokens:** unknown
- **Requests:**
  - **1.**
    - **Task key:** pr:3928c5456f1e0671ef1c935dc5232064:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 123611
    - **Usage:**
      - **Input tokens:** 23676
      - **Output tokens:** 3365
      - **Total tokens:** 27041
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 3085
    - **Consumption unknown:** False
    - **Status:** completed
  - **2.**
    - **Task key:** pr:655ee1ddc6e99eba1394de8cc2a26d3d:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 130572
    - **Usage:**
      - **Input tokens:** 27635
      - **Output tokens:** 6225
      - **Total tokens:** 33860
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 5766
    - **Consumption unknown:** False
    - **Status:** completed
  - **3.**
    - **Task key:** pr:197db26be746310ee6246b9775f4bf60:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 130735
    - **Usage:**
      - **Input tokens:** 27213
      - **Output tokens:** 6539
      - **Total tokens:** 33752
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 6253
    - **Consumption unknown:** False
    - **Status:** completed
  - **4.**
    - **Task key:** pr:ed90025d7a8e2d8c33474a1344abb146:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 125647
    - **Usage:**
      - **Input tokens:** 26292
      - **Output tokens:** 13583
      - **Total tokens:** 39875
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 13252
    - **Consumption unknown:** False
    - **Status:** completed
  - **5.**
    - **Task key:** pr:66e7b296be450fb4e7cf04111dd36363:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 118756
    - **Usage:**
      - **Input tokens:** 24692
      - **Output tokens:** 7686
      - **Total tokens:** 32378
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 7402
    - **Consumption unknown:** False
    - **Status:** completed
  - **6.**
    - **Task key:** pr:ef58975f6ef16a9bb5b9ad6586913d05:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 132777
    - **Usage:**
      - **Input tokens:** 26190
      - **Output tokens:** 11737
      - **Total tokens:** 37927
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 11544
    - **Consumption unknown:** False
    - **Status:** completed
  - **7.**
    - **Task key:** pr:d41819ca7fbc19d985853b9025b3b306:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 121649
    - **Usage:**
      - **Input tokens:** 24895
      - **Output tokens:** 11927
      - **Total tokens:** 36822
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 11601
    - **Consumption unknown:** False
    - **Status:** completed
  - **8.**
    - **Task key:** pr:7a9cfeda51fcad343faf7e998ac75af9:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 114216
    - **Usage:**
      - **Input tokens:** 22198
      - **Output tokens:** 14804
      - **Total tokens:** 37002
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 14549
    - **Consumption unknown:** False
    - **Status:** completed
  - **9.**
    - **Task key:** pr:094ad04a7ddb9595e9b4625b681a9282:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 115666
    - **Usage:**
      - **Input tokens:** 23941
      - **Output tokens:** 8413
      - **Total tokens:** 32354
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 8128
    - **Consumption unknown:** False
    - **Status:** completed
  - **10.**
    - **Task key:** pr:197db26be746310ee6246b9775f4bf60:round:1
    - **Stage:** analysis
    - **Reserved tokens:** 157275
    - **Usage:**
      - **Input tokens:** 33489
      - **Output tokens:** 10344
      - **Total tokens:** 43833
      - **Cache read tokens:** 27121
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 10025
    - **Consumption unknown:** False
    - **Status:** completed
  - **11.**
    - **Task key:** pr:1e46b8928981afc2907f0880bd887135:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 123761
    - **Usage:**
      - **Input tokens:** 26055
      - **Output tokens:** 11261
      - **Total tokens:** 37316
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 11148
    - **Consumption unknown:** False
    - **Status:** completed
  - **12.**
    - **Task key:** pr:66e7b296be450fb4e7cf04111dd36363:round:1
    - **Stage:** analysis
    - **Reserved tokens:** 155920
    - **Usage:**
      - **Input tokens:** 32962
      - **Output tokens:** 13963
      - **Total tokens:** 46925
      - **Cache read tokens:** 24689
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 13854
    - **Consumption unknown:** False
    - **Status:** completed
  - **13.**
    - **Task key:** pr:ef58975f6ef16a9bb5b9ad6586913d05:round:1
    - **Stage:** analysis
    - **Reserved tokens:** 144984
    - **Usage:**
      - **Input tokens:** 29241
      - **Output tokens:** 13430
      - **Total tokens:** 42671
      - **Cache read tokens:** 26097
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 13144
    - **Consumption unknown:** False
    - **Status:** completed
  - **14.**
    - **Task key:** pr:bec21d641a0ccbbb7dc70e2d4e915b74:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 134635
    - **Usage:**
      - **Input tokens:** 27966
      - **Output tokens:** 9965
      - **Total tokens:** 37931
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 9684
    - **Consumption unknown:** False
    - **Status:** completed
  - **15.**
    - **Task key:** pr:cd47d4774664ce2b2ceb98d1e300298b:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 121987
    - **Usage:**
      - **Input tokens:** 26014
      - **Output tokens:** 13811
      - **Total tokens:** 39825
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 13485
    - **Consumption unknown:** False
    - **Status:** completed
  - **16.**
    - **Task key:** pr:094ad04a7ddb9595e9b4625b681a9282:round:1
    - **Stage:** analysis
    - **Reserved tokens:** 147429
    - **Usage:**
      - **Input tokens:** 31098
      - **Output tokens:** 16384
      - **Total tokens:** 47482
      - **Cache read tokens:** 23921
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 16382
    - **Consumption unknown:** False
    - **Status:** failed
    - **Code:** output\_limit
    - **Retryable:** False
    - **Retry after:** unavailable
  - **17.**
    - **Task key:** pr:da695df77db23882831b4c5ce9df5e69:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 124160
    - **Usage:**
      - **Input tokens:** 25448
      - **Output tokens:** 10947
      - **Total tokens:** 36395
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 10829
    - **Consumption unknown:** False
    - **Status:** completed
  - **18.**
    - **Task key:** pr:1e46b8928981afc2907f0880bd887135:round:1
    - **Stage:** analysis
    - **Reserved tokens:** 124257
    - **Usage:**
      - **Input tokens:** 26184
      - **Output tokens:** 12044
      - **Total tokens:** 38228
      - **Cache read tokens:** 25969
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 11927
    - **Consumption unknown:** False
    - **Status:** completed
  - **19.**
    - **Task key:** pr:84379efe7095859d8c40a083ba136b9d:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 131737
    - **Usage:**
      - **Input tokens:** 26978
      - **Output tokens:** 6797
      - **Total tokens:** 33775
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 6506
    - **Consumption unknown:** False
    - **Status:** completed
  - **20.**
    - **Task key:** pr:bec21d641a0ccbbb7dc70e2d4e915b74:round:1
    - **Stage:** analysis
    - **Reserved tokens:** 154877
    - **Usage:**
      - **Input tokens:** 32860
      - **Output tokens:** 14516
      - **Total tokens:** 47376
      - **Cache read tokens:** 27889
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 14146
    - **Consumption unknown:** False
    - **Status:** completed
  - **21.**
    - **Task key:** pr:66e7b296be450fb4e7cf04111dd36363:round:2
    - **Stage:** analysis
    - **Reserved tokens:** 169638
    - **Usage:**
      - **Input tokens:** 35872
      - **Output tokens:** 13245
      - **Total tokens:** 49117
      - **Cache read tokens:** 24689
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 12957
    - **Consumption unknown:** False
    - **Status:** completed
  - **22.**
    - **Task key:** pr:ef58975f6ef16a9bb5b9ad6586913d05:round:2
    - **Stage:** analysis
    - **Reserved tokens:** 200546
    - **Usage:**
      - **Input tokens:** 41088
      - **Output tokens:** 16384
      - **Total tokens:** 57472
      - **Cache read tokens:** 26097
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 16382
    - **Consumption unknown:** False
    - **Status:** failed
    - **Code:** output\_limit
    - **Retryable:** False
    - **Retry after:** unavailable
  - **23.**
    - **Task key:** pr:84379efe7095859d8c40a083ba136b9d:round:1
    - **Stage:** analysis
    - **Reserved tokens:** 179836
    - **Usage:**
      - **Input tokens:** 37764
      - **Output tokens:** 16384
      - **Total tokens:** 54148
      - **Cache read tokens:** 26865
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 16382
    - **Consumption unknown:** False
    - **Status:** failed
    - **Code:** output\_limit
    - **Retryable:** False
    - **Retry after:** unavailable
  - **24.**
    - **Task key:** pr:094ad04a7ddb9595e9b4625b681a9282:unit:264be3d831e56d3c:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 162819
    - **Usage:**
      - **Input tokens:** 30224
      - **Output tokens:** 6470
      - **Total tokens:** 36694
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 6369
    - **Consumption unknown:** False
    - **Status:** completed
  - **25.**
    - **Task key:** pr:1e46b8928981afc2907f0880bd887135:round:2
    - **Stage:** analysis
    - **Reserved tokens:** 124575
    - **Usage:**
      - **Input tokens:** 26291
      - **Output tokens:** 16384
      - **Total tokens:** 42675
      - **Cache read tokens:** 26097
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 16382
    - **Consumption unknown:** False
    - **Status:** failed
    - **Code:** output\_limit
    - **Retryable:** False
    - **Retry after:** unavailable
  - **26.**
    - **Task key:** pr:da695df77db23882831b4c5ce9df5e69:round:1
    - **Stage:** analysis
    - **Reserved tokens:** 140742
    - **Usage:**
      - **Input tokens:** 29130
      - **Output tokens:** 14294
      - **Total tokens:** 43424
      - **Cache read tokens:** 25329
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 13998
    - **Consumption unknown:** False
    - **Status:** completed
  - **27.**
    - **Task key:** pr:094ad04a7ddb9595e9b4625b681a9282:unit:35ed4f083687aaea:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 162819
    - **Usage:**
      - **Input tokens:** 30210
      - **Output tokens:** 7756
      - **Total tokens:** 37966
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 7662
    - **Consumption unknown:** False
    - **Status:** completed
  - **28.**
    - **Task key:** pr:ef58975f6ef16a9bb5b9ad6586913d05:unit:2b0ce09d11f38465:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 215936
    - **Usage:**
      - **Input tokens:** 40218
      - **Output tokens:** 15540
      - **Total tokens:** 55758
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 14933
    - **Consumption unknown:** False
    - **Status:** completed
  - **29.**
    - **Task key:** pr:bec21d641a0ccbbb7dc70e2d4e915b74:round:2
    - **Stage:** analysis
    - **Reserved tokens:** 207721
    - **Usage:**
      - **Input tokens:** 45145
      - **Output tokens:** 16384
      - **Total tokens:** 61529
      - **Cache read tokens:** 27889
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 16295
    - **Consumption unknown:** False
    - **Status:** failed
    - **Code:** output\_limit
    - **Retryable:** False
    - **Retry after:** unavailable
  - **30.**
    - **Task key:** pr:094ad04a7ddb9595e9b4625b681a9282:unit:507fd9ab942e1c8f:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 162819
    - **Usage:**
      - **Input tokens:** 30224
      - **Output tokens:** 9564
      - **Total tokens:** 39788
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 9463
    - **Consumption unknown:** False
    - **Status:** completed
  - **31.**
    - **Task key:** pr:1e46b8928981afc2907f0880bd887135:unit:5f633dcfbffd08e4:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 139965
    - **Usage:**
      - **Input tokens:** 25401
      - **Output tokens:** 3083
      - **Total tokens:** 28484
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 2987
    - **Consumption unknown:** False
    - **Status:** completed
  - **32.**
    - **Task key:** pr:1e46b8928981afc2907f0880bd887135:unit:0b9cb563f7ae6bf8:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 139965
    - **Usage:**
      - **Input tokens:** 25401
      - **Output tokens:** 11319
      - **Total tokens:** 36720
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 10552
    - **Consumption unknown:** False
    - **Status:** completed
  - **33.**
    - **Task key:** pr:84379efe7095859d8c40a083ba136b9d:unit:ff467ba033fab46f:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 194666
    - **Usage:**
      - **Input tokens:** 36564
      - **Output tokens:** 15784
      - **Total tokens:** 52348
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 14366
    - **Consumption unknown:** False
    - **Status:** completed
  - **34.**
    - **Task key:** pr:ef58975f6ef16a9bb5b9ad6586913d05:unit:18b1cfdd6f9edf34:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 215936
    - **Usage:**
      - **Input tokens:** 40216
      - **Output tokens:** 14504
      - **Total tokens:** 54720
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 14081
    - **Consumption unknown:** False
    - **Status:** completed
  - **35.**
    - **Task key:** pr:da695df77db23882831b4c5ce9df5e69:round:2
    - **Stage:** analysis
    - **Reserved tokens:** 158196
    - **Usage:**
      - **Input tokens:** 33526
      - **Output tokens:** 16384
      - **Total tokens:** 49910
      - **Cache read tokens:** 25329
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 16382
    - **Consumption unknown:** False
    - **Status:** failed
    - **Code:** output\_limit
    - **Retryable:** False
    - **Retry after:** unavailable
  - **36.**
    - **Task key:** pr:094ad04a7ddb9595e9b4625b681a9282:unit:75e99ebd751ffbcb:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 162819
    - **Usage:**
      - **Input tokens:** 30210
      - **Output tokens:** 13387
      - **Total tokens:** 43597
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 13293
    - **Consumption unknown:** False
    - **Status:** completed
  - **37.**
    - **Task key:** pr:bec21d641a0ccbbb7dc70e2d4e915b74:unit:0fa87d3d36e3a3ae:round:0
    - **Stage:** analysis
    - **Reserved tokens:** 222971
    - **Usage:**
      - **Input tokens:** unavailable
      - **Output tokens:** unavailable
      - **Total tokens:** unavailable
      - **Cache read tokens:** unavailable
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** unavailable
    - **Consumption unknown:** True
    - **Status:** failed
    - **Code:** provider\_http\_503
    - **Retryable:** False
    - **Retry after:** unavailable
  - **38.**
    - **Task key:** 66f020d4525e575b55e83e4535a5beb3352666bf62805a3c5a260bf58c8c7676:0
    - **Stage:** assessment
    - **Reserved tokens:** 143463
    - **Usage:**
      - **Input tokens:** 26515
      - **Output tokens:** 5822
      - **Total tokens:** 32337
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 4908
    - **Consumption unknown:** False
    - **Status:** completed
  - **39.**
    - **Task key:** 01dbcc1a20f10480e9ec468fe343ca5cc236030d0bfbc8e5349c82f6d98fce47:0
    - **Stage:** assessment
    - **Reserved tokens:** 48536
    - **Usage:**
      - **Input tokens:** 7120
      - **Output tokens:** 3049
      - **Total tokens:** 10169
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 2643
    - **Consumption unknown:** False
    - **Status:** completed
  - **40.**
    - **Task key:** 14852dde9185553e62944a9974cc8beffe8c4ff90fde4d432977521380979b2c:0
    - **Stage:** assessment
    - **Reserved tokens:** 113532
    - **Usage:**
      - **Input tokens:** 20766
      - **Output tokens:** 5475
      - **Total tokens:** 26241
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 4813
    - **Consumption unknown:** False
    - **Status:** completed
  - **41.**
    - **Task key:** 01dbcc1a20f10480e9ec468fe343ca5cc236030d0bfbc8e5349c82f6d98fce47:1
    - **Stage:** assessment
    - **Reserved tokens:** 103339
    - **Usage:**
      - **Input tokens:** 20193
      - **Output tokens:** 2904
      - **Total tokens:** 23097
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 2473
    - **Consumption unknown:** False
    - **Status:** completed
  - **42.**
    - **Task key:** 9510bc0b48cfa967312ca463833da8cfa95df000eb680f3a012a5718c7029481:0
    - **Stage:** assessment
    - **Reserved tokens:** 168020
    - **Usage:**
      - **Input tokens:** 31888
      - **Output tokens:** 5265
      - **Total tokens:** 37153
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 4574
    - **Consumption unknown:** False
    - **Status:** completed
  - **43.**
    - **Task key:** 3adcdac9c87f4ef7c11e17b9e8bd565d951af3e3d94c41bd5d534933f023bdab:0
    - **Stage:** assessment
    - **Reserved tokens:** 64123
    - **Usage:**
      - **Input tokens:** 10519
      - **Output tokens:** 4031
      - **Total tokens:** 14550
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 3473
    - **Consumption unknown:** False
    - **Status:** completed
  - **44.**
    - **Task key:** 3450655b1eff2e3b0a2e71f6fbe4c912a16f90ffef9fcc9c3df300f5e0d0fd2f:0
    - **Stage:** assessment
    - **Reserved tokens:** 122507
    - **Usage:**
      - **Input tokens:** 22782
      - **Output tokens:** 5025
      - **Total tokens:** 27807
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 4209
    - **Consumption unknown:** False
    - **Status:** completed
  - **45.**
    - **Task key:** caf51e5207baeeabcf67dbc90290bc23df9c74e8c31fce6ca2e0ba232d2c1da0:0
    - **Stage:** assessment
    - **Reserved tokens:** 132056
    - **Usage:**
      - **Input tokens:** 24826
      - **Output tokens:** 4299
      - **Total tokens:** 29125
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 3741
    - **Consumption unknown:** False
    - **Status:** completed
  - **46.**
    - **Task key:** caf51e5207baeeabcf67dbc90290bc23df9c74e8c31fce6ca2e0ba232d2c1da0:1
    - **Stage:** assessment
    - **Reserved tokens:** 158448
    - **Usage:**
      - **Input tokens:** 31319
      - **Output tokens:** 5835
      - **Total tokens:** 37154
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 5104
    - **Consumption unknown:** False
    - **Status:** completed
  - **47.**
    - **Task key:** caf51e5207baeeabcf67dbc90290bc23df9c74e8c31fce6ca2e0ba232d2c1da0:2
    - **Stage:** assessment
    - **Reserved tokens:** 225190
    - **Usage:**
      - **Input tokens:** 45917
      - **Output tokens:** 8970
      - **Total tokens:** 54887
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 8065
    - **Consumption unknown:** False
    - **Status:** completed
  - **48.**
    - **Task key:** 75eaffa09a0a6b5c352acb6b1d6c3bf3fafa609d
    - **Stage:** summary
    - **Reserved tokens:** 73953
    - **Usage:**
      - **Input tokens:** 14591
      - **Output tokens:** 7247
      - **Total tokens:** 21838
      - **Cache read tokens:** 0
      - **Cache write tokens:** unavailable
      - **Reasoning tokens:** 5696
    - **Consumption unknown:** False
    - **Status:** completed
- **Reserved tokens:** 2044447
- **Budget exceeded:** False
