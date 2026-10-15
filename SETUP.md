# Ceylon Dread – අලුත් system එක (Setup guide)

හැමදාම **episodes 2ක්**: එක් එක් episode එකට **long video (විනාඩි 8-10) + Shorts 2** →
YouTube (long + Shorts), Facebook (Page video + Reels), TikTok (draft). සම්පූර්ණයෙන්ම free tools.

```
Gemini (free) → script  →  quality gate  →  AI images (free providers)  →  Edge-TTS voice (free)
   → ffmpeg: Ken Burns + grain + captions + ambience + loudness → thumbnail → upload
```

## 0. එක පාරක් install
```
pip install -r requirements.txt
copy .env.example .env      (.env එක පුරවන්න)
```

## 1. Keys (හැම එකක්ම free)
| මොකටද | කොහෙන්ද | .env නම |
|---|---|---|
| Script ලියන්න | https://aistudio.google.com/apikey | `GEMINI_API_KEY` |
| රූප (හොඳම) | https://enter.pollinations.ai (free tier key) | `POLLINATIONS_API_KEY` |
| රූප (backup) | Cloudflare Workers AI (free daily quota) | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` |
| රූප (backup) | https://huggingface.co/settings/tokens | `HF_TOKEN` |
| රූප (key නැතුව) | Stable Horde – automatic, ඒත් හරිම slow | – |

**Key එකකුවත් නැතුව Horde වලින් රූප හැදෙනවා, ඒත් quality අඩුයි, ඒ නිසා Pollinations/Cloudflare key එකක් ගන්න.**

## 2. පළමු test (publish නොකර)
```
python tests/smoke_render.py                       # render engine test (key ඕන නෑ)
python run_daily.py --count 1 --no-publish         # සැබෑ episode එකක් හදලා output/ready/ බලන්න
```

## 3. YouTube (එක පාරක්)
1. https://console.cloud.google.com → project එකක් → **YouTube Data API v3** enable
2. OAuth consent screen (External, ඔයාගේ Gmail test user විදිහට add කරන්න)
3. Credentials → **OAuth client ID → Desktop app** → JSON download → `client_secret.json` නමින් project folder එකට
4. `python setup_youtube_auth.py` → browser එකේ allow කරන්න → `token.json` හැදෙනවා
5. **වැදගත්:** Google project එක "Testing" mode එකේ නම් token එක දවස් 7කින් expire වෙනවා. OAuth consent screen එක **"In production"** කරන්න (personal use එකට verification ඕන නෑ).
6. YouTube **API quota** = දවසකට 10,000. Upload එකක් 1,600. දවසට 6 uploads (2 long + 4 shorts) = ~9,700, **ඊට වැඩියෙන් බෑ**.
7. Thumbnail upload වෙන්නේ channel එක **phone verify** කරලා නම් විතරයි.

## 4. Facebook
Meta for Developers app එකක් → Page access token (`pages_manage_posts`, `pages_read_engagement`, `publish_video`) → **long-lived Page token** හදාගන්න.
`.env`: `FACEBOOK_PAGE_ID`, `FACEBOOK_ACCESS_TOKEN`.
(මේ API code එක test කරලා නෑ, ඔයාගේ Page එකෙන් මුල් පරීක්ෂාව කරන්න.)

## 5. TikTok (සීමාවක් තියෙනවා)
TikTok API එකෙන් **auto-public-post** කරන්න ඔයාගේ app එකට TikTok audit pass වෙන්න ඕන. ඒ වෙනකම් `TIKTOK_ACCESS_TOKEN` දැම්මොත් video එක **TikTok inbox එකට draft** විදිහට යනවා (ඔයා Post tap කරන්න ඕන).
Token නැත්නම් shorts `output/ready/<story>/…_short1.mp4` (GitHub Actions නම් artifact එකේ) manual upload කරන්න.

## 6. Automation (දෙකෙන් එකක්)
**A. GitHub Actions (PC එක on ඕන නෑ)**
Repo එකට push → Settings → Secrets → ඉහත keys ඔක්කොම secrets විදිහට (`YOUTUBE_TOKEN_JSON` = token.json content).
Private repo = මාසයට free minutes 2,000 (දවසකට ~60 min යනවා, ඒ නිසා tight). Public repo = unlimited (secrets ආරක්ෂිතයි, code විතරක් public).
**`legacy/` folder එකේ පරණ leaked key එක තියෙනවා. ඒක push කරන්න එපා (.gitignore එකේ තියෙනවා), ඒ වගේම ElevenLabs key එක revoke කරන්න.**
**B. ඔයාගේ PC** – Windows Task Scheduler → `run_daily.bat` දවසකට එක පාරක්.

## 7. Publish modes (`PUBLISH_MODE`)
- `scheduled` (default): private upload → `PUBLISH_SLOTS_UTC` වෙලාවට (12:00 / 19:00 SL) auto public. **අතරේ ඔයාට බලලා අකමැති ඒවා delete කරන්න පුළුවන්.**
- `public`: ඉක්මනින්ම public. `private`: private විතරයි.

## 8. YouTube monetization – අවංක කතාව
- හැමදාම AI-generated video 2ක් auto-post කිරීම YouTube ගේ **"inauthentic / mass-produced content"** policy එකට අවදානම්. ඒකට අපි: හැම script එකම අලුත් story එකක්, repetition check, fake-claims block, AI disclosure label, review window දීලා තියෙනවා. ඒත් **guarantee එකක් නෑ**.
- හොඳම ක්‍රමය: සතියකට වරක් ඔයා script/voice එකක් ඔයාගේම දාන එක, comments වලට reply කරන එක, අදහස් වෙනස් කරන එක (human touch).
- YPP: subscribers 1,000 + (public watch hours 4,000 හෝ Shorts views 10M/90 days). Shorts වලින් RPM අඩුයි, long video (8+ min) වලින් mid-roll ads.
- Voice එක Edge-TTS (free). Revenue ආවම ElevenLabs / අලුත් image-video models add කරන්න `modules/tts.py` සහ `modules/images.py` එකේ provider එකක් වෙනස් කරන එක විතරයි.
