Your previous extraction failed these validation checks:
{validation_errors}

Previous output:
{previous_json}

Look at the document again and re-check ONLY the fields involved in these
errors. If the document itself is inconsistent (for example, the printed total
really does not match the items), keep the values as printed and set
"document_inconsistent": true. Do not change values just to make the math
work. Return the full corrected JSON only.
