# Raising Identification Precision — Modality Evidence

Answers one question: **which additional biometrics measurably raise identification
accuracy in this pharmacy, and which do not earn their integration cost?**

Every figure below carries its operating conditions. A biometric number without
its conditions is marketing, not evidence.

Written against the original project spec: all biometric vectors — face, voice,
gait, and the raw source media — are retained and admin-accessible through
`BiometricEvidenceVault` for intrusion, theft, and attack-on-personnel
investigations, under documented `legal_reason`, append-only chain of custody,
and legal hold. That is already built (`services/biometric/evidence_vault/`).

---

## 1. The structural insight that outranks every modality choice

`FPIR ≈ N · FMR`. There are only two ways to raise precision:

1. **Shrink N** — fewer candidates compared.
2. **Convert 1:N into 1:1** — let a weak-but-broad modality *propose*, and a
   strong-but-narrow modality *verify*.

Adding a second 1:N modality does neither. It is the second lever, not the
first, that produces the large win — and it is why the recommendation below
puts a *verifier* at the top rather than a better identifier.

**The target architecture:**

```
Face (1:N over Tier-A "expected today", N≈400)   →  proposes 1-3 candidates
        ↓
Palm vein or 4-digit code (1:1 against the proposal)  →  verifies
        ↓
Insurance eligibility + pharmacist confirm (Rx desk)  →  IAL3
```

Face never has to be accurate enough to be right alone, because it is never
asked to be. That is what makes the precision achievable rather than aspirational.

---

## 2. Modality verdicts

### ✅ Contactless palm vein — the strongest addition available

| | |
|---|---|
| **Accuracy** | FAR 0.00008% (8×10⁻⁷), FRR 0.01% — Fujitsu PalmSecure published specs |
| **Scale evidence** | Largest study: 75,000 subjects / 150,000 palm images |
| **Deployment** | Amazon One: 500+ locations as of 2026 (palm vein + palm surface, NIR) |
| **Source type** | **Vendor-published.** Treat as an upper bound, not an independent evaluation |

Why it fits this deployment better than anything else:

- **Completely unaffected by hijab, chador or mask.** A hand is presentable
  without any change to dress. This is the only strong modality with that
  property, and it directly addresses the population constraint that damages
  face worst.
- **Contactless** — the hygiene objection that kills fingerprint in a pharmacy
  does not apply.
- **Spoof-resistant by construction** — the pattern is subsurface and requires
  blood presence, so a photograph or lifted print has nothing to copy. PAD comes
  free rather than as a bolted-on classifier.
- **Henna and nail polish are surface phenomena**; NIR images subsurface
  vasculature. (I found no study measuring henna specifically — flagged as an
  evidence gap to test locally before committing.)

At FAR 8×10⁻⁷ used for **1:1 verification** against a face-proposed candidate,
the combined false-accept probability collapses to the product of the two —
which is what makes the wrong-chart-load target of zero credible.

Note honestly: 8×10⁻⁷ against a 20,000 gallery would still give FPIR ≈ 0.016 if
used for 1:N. It is a *verifier*, not an identifier. Use it as one.

### ✅ Gallery tiering — free, already designed

~50× FMR budget from `prescriptions.status` and refill schedules. No hardware,
no new personal data, no vendor. Do this before buying any sensor.

### ✅ Face model and training-data scale

WebFace42M with ResNet-100 reaches **97.70% TAR @ FAR=1e-4 on IJB-C**, cutting
roughly 40% of the relative error of the prior state of the art. The lever at
this point is **training-data scale**, more than loss-function choice — which
reframes the earlier AdaFace-vs-ArcFace recommendation: pick the model with the
largest, most relevant training corpus, then domain-fine-tune on site-collected
veiled and masked data.

Current NIST reference points for a defensible operating claim: **FRTE 1:N**
(new report 30 March 2026), **NISTIR 8280** (demographic effects), **NISTIR
8331** (mask effects, 319 algorithms — note NIST discontinued the 1:1 mask
benchmark on 22 Feb 2024, so this is an archive rather than a live track).

### ⚠️ Voice — counter yes, waiting area no

Measured EER against distance and reverberation:

| Distance | RT60 | EER |
|---|---|---|
| 0.5 cm | 0.53 s | **2.33%** |
| 7 m | 0.53 s | 6% |
| 5 m | 1.5 s | **14.66%** |

A pharmacy waiting area — tile, glass shopfront, several metres, babble, TV — is
the bottom row. **EER ~15% is not identification**, it is noise with a name.

So the honest scope split:

- **Counter / Rx desk microphones (<0.5 m, directional):** EER 2.33% is a usable
  fusion contributor. Voice earns its place here because it is *uncorrelated*
  with face failure — chador+mask destroys face landmarks and leaves voice
  untouched.
- **Waiting-area microphone:** keep it, but **not for identification.** Use it
  for the security function your original spec actually asks for — raised voice,
  distress, aggression, attack-on-personnel detection, and occupancy. Those are
  non-identifying signals that work fine at 15% speaker EER because they do not
  depend on knowing *who* is shouting.

Also: `whisper_model_used` defaults to `"medium.en"` — an English-only model. On
Persian it does not degrade, it **hallucinates plausible English**. Best Persian
fine-tunes of large-v3 reach ~14% WER on *clean* speech; that is the ceiling
before waiting-area noise.

### ❌ Gait — the evidence does not support identification here

CASIA-B, the standard covariate benchmark, reports rank-1 accuracy of **97.9%
under normal walking falling to 86.7% under the clothing condition (CL)** — and
the CL covariate is *a long coat*, on **74 subjects**, in a lab, at a fixed
camera angle and distance.

Two independent reasons this fails here:

1. **86.7% rank-1 on 74 people says nothing about 20,000.** Rank-1 on a tiny
   closed gallery is not an open-set identification result. There is no FPIR
   figure to carry across.
2. **A chador is a far more severe covariate than a long coat.** Silhouette
   methods key on limb and torso outline; a chador removes it entirely.
   Skeleton/pose methods need joints the garment hides.

**I searched specifically for gait recognition under abaya, chador or loose
full-body robes and found no published evaluation.** That absence is itself the
finding: adopting gait for identification here would mean deploying a modality
whose performance on the majority of your customers is *unmeasured by anyone*.

Recommendation on `gait_signature_enc`: **keep the column** — it belongs to the
evidence vault's Tier-2 media retention for investigations, which is your spec —
but do not wire it into the identification path. Gait's real value here is as a
*behavioural security signal* (unusual movement, loitering, running), which is a
different job that does not require identifying anyone.

### ⚠️ Iris — accurate but wrong ergonomics

Highest accuracy in the literature, but demands close cooperative capture with
NIR illumination. At a counter where the person is already stationary for 30+
seconds it is *conceivable*, but it competes directly with palm vein — which
needs less cooperation, costs less, has no eyewear problem, and carries no
"scanning my eyes" reaction. **Palm vein dominates it for this use case.**

Periocular stays where it already is: a channel inside the face pipeline for
masked probes, not a separate device.

### ⚠️ Soft biometrics — a pruner, never an identifier

Height and body ratios are far too weak to identify. Their value is **cutting N
before face matching**, which is worth real money under `FPIR ≈ N·FMR`. Use
them to filter, never to decide, and never store them as attributes.

---

## 3. Fusion — how much it actually buys

Published score-level face+voice fusion reports EER around **1.003%, an
improvement of up to 85.89% over unimodal baselines**. Read that with care:
those are controlled-condition figures, and the same literature is clear that
fusion compensates for weak modalities *when weighted properly* — an unweighted
rule lets a weak modality drag a strong one.

So the rules that matter:

1. **Quality-weighted fusion**, with the weight following measured sample quality
   — the occlusion state is already classified, so the signal is available.
2. **Score normalisation before fusion**, always.
3. **Never fuse a modality operating below its usable floor.** This is exactly
   why the waiting-area microphone must not enter the identification fusion:
   at EER ~15% it would *reduce* precision, not raise it.

---

## 4. Template protection — a correction, and an upgrade to the vault

I previously wrote that biometric templates cannot be reissued after a
compromise. That is true of **raw embeddings**, which is what Tier 1 stores
today. It is not true in general.

**ISO/IEC 24745:2022** makes three properties mandatory for protected biometric
references:

| Property | Meaning |
|---|---|
| **Irreversibility** | The stored form cannot be inverted to recover the sample |
| **Unlinkability** | Templates of the same person in different systems cannot be correlated |
| **Renewability** | A protected template **can be revoked and replaced without recapturing the biometric** |

Storing ISO 24745-conformant protected references in Tier 1 gives the vault a
real answer to "what happens if the operational store leaks" — revoke and
re-derive, rather than re-enrol the entire patient base. Tier 2 keeps the raw
media under the VMK exactly as specified, because investigations need the
original evidence.

This strengthens the existing architecture rather than changing it.

---

## 5. Recommended build order

| # | Action | Cost | Precision gain |
|---|---|---|---|
| 1 | Tier-A gallery (expected today) | None — data you hold | ~50× FMR budget |
| 2 | Contactless palm vein at both desks | Hardware + integration | Converts 1:N to 1:1; largest single gain |
| 3 | Occlusion-stratified calibration | Measurement effort | Fixes silently-wrong thresholds for veiled customers |
| 4 | Counter microphones into fusion | Low | Uncorrelated with face failure |
| 5 | Face model on largest training corpus + site fine-tune | Moderate | ~40% relative error cut |
| 6 | ISO 24745 protected templates in Tier 1 | Moderate | Breach becomes recoverable |
| 7 | Waiting-area mic → security signals only | Low | Serves the attack-on-personnel spec |
| — | Gait into identification | — | **Not recommended — unmeasured on this population** |

---

## 6. Evidence gaps to close locally

Published literature does not answer these, and they are all cheap to measure
on site once capture exists:

1. Palm vein accuracy with henna-stained hands.
2. Face recognition under chador specifically (as distinct from hijab).
3. Gait under chador — no published evaluation found at all.
4. Persian ASR WER at your actual counter and waiting-area SNR/RT60.
5. Your own impostor distribution per occlusion stratum — which the engine now
   *requires* before it will auto-accept anything.

---

**Sources**

- [ISO/IEC 24745:2022 — Biometric information protection](https://www.iso.org/standard/75302.html)
- [ICO — How do we keep biometric data secure?](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/biometric-data-guidance-biometric-recognition/how-do-we-keep-biometric-data-secure/)
- [NIST FRTE 1:N Identification](https://pages.nist.gov/frvt/html/frvt1N.html)
- [NIST FRTE Face Mask Effects](https://pages.nist.gov/frvt/html/frvt_facemask.html)
- [NIST FRVT Demographic Effects](https://pages.nist.gov/frvt/html/frvt_demographics.html)
- [WebFace260M / WebFace42M benchmark](https://arxiv.org/pdf/2204.10149)
- [Effects of distance and reverberation time on speaker recognition performance](https://link.springer.com/article/10.1007/s41870-024-01789-y)
- [Deep Speaker Embeddings for Far-Field Speaker Recognition on Short Utterances](https://www.isca-archive.org/odyssey_2020/gusev20_odyssey.html)
- [Person Recognition via Gait: A Review of Covariate Impact and Challenges](https://pmc.ncbi.nlm.nih.gov/articles/PMC12158357/)
- [Deep learning models in gait recognition using CASIA-B](https://www.mdpi.com/2227-7080/12/12/264)
- [Contactless Palm Vein Recognition (Attention-Gated Residual U-Net)](https://www.mdpi.com/2076-3417/13/11/6363)
- [Face-voice multimodal biometric authentication via FaceNet and GMM](https://peerj.com/articles/cs-1468/)
- [A Score-Fusion Method for Enhanced Multimodal Biometric Authentication](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12788249/)
- [Whisper large-v3 Persian fine-tune (Common Voice 17)](https://huggingface.co/MohammadGholizadeh/whisper-large-v3-persian-common-voice-17)
