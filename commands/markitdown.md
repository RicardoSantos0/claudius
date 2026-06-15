---
description: Convert a document to Markdown with the user-global Microsoft MarkItDown executable
allowed-tools: Read, Write, Bash(markitdown:*)
---

# /markitdown

Convert a source document to Markdown using the user-global Microsoft
MarkItDown executable.

## Usage

```
/markitdown path/to/file.pdf [optional-output.md]
```

## Steps

$ARGUMENTS

1. Parse the first argument as the source path.
2. If a second argument is provided, use it as the output path. Otherwise write
   next to the source as `<stem>.converted.md`.
3. Run:

```bash
markitdown "$SOURCE" -o "$OUTPUT"
```

> `markitdown` is resolved from `PATH`. If it is not on `PATH`, install it
> (`uv tool install markitdown` or `pipx install markitdown`) or invoke it via
> its absolute path in a local-only override.

4. Read the generated Markdown and briefly summarize what was converted.
5. Tell the user the output path.

If conversion fails, show the exact command and error. Do not silently invent
content from the original file.
