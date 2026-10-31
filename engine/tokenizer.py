"""Plain-Python tokenizer for step 1. Rules, in order (first match wins at each position):

1. slash date            4/30/2024            one token
2. letters + 'digits     FY'21                one token
3. 'digits               '23                  one token
4. clitic                's 're 'll 've 'd 'm 't   own token ("ExampleCo's" -> ExampleCo, 's)
5. number with commas/decimals   1,200   3.5  one token
6. alphanumeric run      FY2019  3Q24  10bn  Q3  2023  revenue   one token (a hyphen or slash splits it: Sample-Co -> Sample, -, Co)
7. any other single non-space character   $  %  ?  ,  .  -  (  )

Glossary phrases are tokenized with the same rules, so a phrase matches exactly when its tokens appear in order.
"""
import re

TOKEN = re.compile(
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r"|[A-Za-z]+['’]\d{2}\b"
    r"|['’]\d{2}\b"
    r"|['’](?:s|re|ll|ve|d|m|t)\b"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+"
    r"|[A-Za-z0-9]+"
    r"|\S"
)


def tokenize(text):
    """Return [(token_text, start_char, end_char)]."""
    return [(m.group(0), m.start(), m.end()) for m in TOKEN.finditer(text)]
