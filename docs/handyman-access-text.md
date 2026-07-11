# Handyman / access-text runbook

Produce a short access text to hand to a tradesperson (plumber, electrician,
cleaner backup…) for any rental, working from **either Smoobu or Beds24**. The
text mirrors what guests get for arrival + key opening, but with guest-specific
lines stripped and the actual PIN left as a `[CODE PIN]` placeholder the owner
fills in by hand.

## Principle

The arrival-instructions template alone is **not enough**. The separate
"code"/"lock" message often carries real entry mechanics the arrival template
omits — e.g. the apartment PIN is entered **followed by `#`**. Always fetch
**both** and merge them.

## Procedure

1. **Fetch** the raw material with the helper (auto-detects the provider by name):

   ```bash
   python3 scripts/access_text_fetch.py "Hippocrate"     # -> Smoobu arrival + lock messages
   python3 scripts/access_text_fetch.py "Le Matisse" --provider beds24
   python3 scripts/access_text_fetch.py --list           # every unit across both providers
   ```

   - **Smoobu**: templates aren't in the API, so the script reads the *arrival*
     and *lock/code* messages actually sent to a recent reservation
     (`GET /reservations/{id}/messages`). Needs a past/booked reservation to
     exist. WAF requires a normal `User-Agent` (the script sets one).
   - **Beds24**: reads `templates.template1` (FR) / `template2` (EN) via
     `GET /properties?id=...&includeTexts=all`. Beds24 igloohome codes are
     dynamic (sent by the Make scenario), so there is **no** stored code
     message — entry mechanics live inside the arrival block itself.

2. **Strip** the lines specific to a normal guest:
   - greeting ("Hi Amélie,"), sign-off
   - "code received in a separate message" / "sent to you separately"
   - "valid from the time of your check-in"
   - guest-app link, upsells, "enjoy your stay"
   - keep WiFi only if a tradesperson plausibly needs it

3. **Merge** the two into one concise access text (this is an LLM/judgement
   step — the script does not do it). Keep: address + floor, building entry,
   in-building wayfinding, apartment-door mechanics **including the `#`-style
   detail from the code message**, help video. Replace the real PIN with
   `[CODE PIN]`. Match the language of the other handyman texts (French).

4. **Output**: print the finished block in chat for copy-paste. The owner drops
   the freshly generated igloohome PIN into `[CODE PIN]`.

## Example output shape

```
Accès appartement — <Nom>
<adresse> — <étage>

Porte de l'immeuble : <interphone / code / appel>
<repérage dans l'immeuble>
Porte de l'appartement : clavier à code — faites le code [CODE PIN] suivi de #.

WiFi : <ssid / mot de passe>   (si utile)
Vidéo d'aide : <url>           (si dispo)
```

## Credentials

From `.env`: `SMOOBU_API_KEY` (Smoobu), `BEDS24_READ_ALL_TOKEN` (Beds24 reads).
See CLAUDE.md for the Beds24 auth model.

Related: [[feedback_handyman_text_merge_both_messages]] (memory).
