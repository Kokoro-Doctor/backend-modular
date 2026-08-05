# Insurance claim PDF: how download works (and why it may not match the screen)

This document describes the **exact path** from the hospital “Medi claim agent” download screen to the saved PDF, and explains common reasons the file **does not look identical** to what you see in the UI.

## Where it lives in the app

| Piece | Location |
|--------|----------|
| Screen | `frontend/screens/HospitalScreens/MediAssistFormA.jsx` |
| HTML template + download | `frontend/utils/MediAssistFormA.js` (`generateMediAssistFormAHTML`, `downloadMediAssistFormA`) |
| Navigation into the screen | e.g. `HospitalInsuranceClaim.jsx` → `navigation.navigate("MediAssistFormA", { analysisData })` |

The screen receives **`route.params.analysisData`**, which includes **`analysisData.structured_data`** from the insurance extraction pipeline. That structured object is mapped into editable **`form`** state via **`buildInitialForm`** in `MediAssistFormA.jsx`.

## Single source of truth for the PDF

The PDF is **not** a screenshot of the React Native form. It is generated from a **string of HTML + CSS** produced by:

- **`generateMediAssistFormAHTML(form, signatureDataUrl)`** in `MediAssistFormA.js`

That function:

1. Takes the same **`form`** object the screen edits (plus an optional **`signatureImage`** data URI from the signature capture).
2. Returns a full HTML document: `<!DOCTYPE html>`, embedded `<style>`, and a root **`.insurance-form-root`** (A4-sized, compact typography, char boxes, tables, etc.).

So **whatever appears in that HTML string is what the PDF is supposed to contain**. The on-screen “editable” layout is a **separate** implementation (React Native `View` / `TextInput` / `CharBoxRow` and StyleSheet styles).

## What happens when the user taps “Download updated claim”

The handler **`handleDownload`** (in `MediAssistFormA.jsx`) calls:

```text
downloadMediAssistFormA(form, signatureImage)
```

So the download always uses **current `form` state** and **the same signature image** the screen holds—there is no second hidden copy of the data for export.

### File name

Inside `downloadMediAssistFormA`:

- **`MediAssistFormA_InsuranceClaim_{primaryName}_{YYYY-MM-DD}.pdf`**, where `primaryName` comes from `form.primaryName`, normalized (spaces → underscores).

### Web (`Platform.OS === "web"`)

1. **`html2pdf.js`** is loaded dynamically.
2. The returned HTML string is **parsed** with `DOMParser`.
3. The **`<style>`** block and **`.insurance-form-root`** are extracted.
4. **Why this exists (important):** `html2pdf` clones the target node into the **main document**. Styles that only live inside an **iframe** `srcDoc` do not apply to that clone, and selectors like `body { }` do not style a `div`. So the code:
   - Appends a **copy of the same CSS** to **`document.head`** (tagged `data-insurance-pdf-export`).
   - Appends an **off-screen host** `div` (fixed at `left: -9999px`, width **210mm**) containing a clone of **`.insurance-form-root`**.
5. **`html2pdf().set({...}).from(captureEl).save(fileName)`** runs on that off-screen element with roughly:
   - **A4 portrait**, **margin 0**
   - **html2canvas** options: **`scale: 3`**, `useCORS: true`, `allowTaint: true`
   - **Image type PNG** (to reduce JPEG artifacts on thin signature strokes)
6. The injected style and host nodes are **removed** in a `finally` block.

So on web, the PDF is effectively: **HTML → html2canvas (raster) → embedded in PDF via jsPDF**, not a “native” print of vector text.

### Native (iOS / Android)

1. **`Print.printToFileAsync({ html })`** with the **same HTML string** (no browser DOM).
2. The temp file is copied into the app document directory with the final filename.
3. **`Sharing.shareAsync`** opens the system share sheet (or an alert with the path if sharing is unavailable).

The native path uses the platform print stack to turn HTML into PDF; it is **not** the same engine as the web `html2canvas` path.

## How the UI relates to that HTML (and why things diverge)

### Wide web layout (desktop: `Platform.OS === "web"` and width &gt; 1000)

The screen can show two modes:

| Mode | What you see | Relation to PDF |
|------|----------------|-----------------|
| **Preview** (default) | An **`<iframe srcDoc={htmlPreview}>`** where `htmlPreview` is **`generateMediAssistFormAHTML(form, signatureImage)`** | **Same HTML/CSS** as the PDF input. This is the closest visual match to the exported file. |
| **Edit** | The **React Native** form (`CharBoxRow`, `TextInput`, etc.) | **Different renderer and styles** from the HTML template—spacing, fonts, logo (RN may use an **image** asset while the HTML template uses **text “Medi Assist”**), etc. The PDF still comes from the HTML generator, not from this view. |

So if you compare the PDF to **Edit mode**, they are **expected to differ**. Compare to **Preview** (iframe) for a fair check.

### Narrow web or native mobile (`Platform.OS !== "web"` or width &lt; 1000)

The UI is the **React Native** form inside a horizontal scroll; there is **no** full-page HTML iframe preview wired the same way as on wide web.

Users only see the RN layout, but the download still uses **`generateMediAssistFormAHTML`**. So **layout, font metrics, and chrome** can differ a lot from the on-screen RN form even when the **field values** match.

## Other reasons the PDF may still differ slightly from the iframe preview (web)

Even when Preview mode shows the same HTML as export:

1. **Rasterization:** Web export runs **html2canvas** at **scale 3** and packs the result into a PDF. That is a **bitmap** snapshot; subpixel rendering, fonts, and flex layout can differ slightly from the live iframe.
2. **Separate DOM clone:** The PDF path renders a **cloned** node in the main document with injected CSS; the iframe preview is a separate document. Rare edge cases (fonts loaded, CORS on images) can diverge.
3. **Signature:** The signature is embedded as a **data URL** image in HTML; scaling inside **`.signature-img`** / html2canvas can look slightly different from the pad UI.

## Backend / insurance extraction (context only)

Upload analysis produces **`structured_data`** consumed when building the initial form. That pipeline is separate from PDF generation; the download step does **not** re-call the backend—it only serializes **`form` + signature** into HTML.

## Summary

- **PDF content** = output of **`generateMediAssistFormAHTML(form, signatureImage)`**.
- **Web file** = that HTML captured via **html2pdf** (html2canvas → jsPDF), with styles injected into the main document on purpose.
- **Native file** = **expo-print** from the same HTML string.
- **Mismatch with UI** is most often because the user is comparing to the **React Native** form or a **non-desktop** layout, not the **iframe Preview**; secondary causes are **canvas rasterization** and **native vs web** PDF engines.

For debugging “looks wrong” reports, confirm **Preview (iframe) on wide web** vs PDF first; if those differ, inspect html2canvas/off-screen clone issues. If only RN vs PDF differs, treat that as **two UIs, one data model** unless you unify rendering.

---

## Form B (hospital section)

Form B follows an identical technical pattern. Key differences:

| Item | Form A | Form B |
|------|--------|--------|
| Screen | `MediAssistFormA.jsx` | `MediAssistFormB.jsx` |
| Service | `MediAssistFormA.js` | `MediAssistFormB.js` |
| Route | `MediAssistFormA` | `MediAssistFormB` |
| HTML generator | `generateMediAssistFormAHTML` | `generateMediAssistFormBHTML` |
| Download fn | `downloadMediAssistFormA` | `downloadMediAssistFormB` |
| PDF filename prefix | `MediAssistFormA_InsuranceClaim_` | `MediAssistFormB_HospitalClaim_` |
| Initial state | Autofilled from `analysisData` | **All blank** (autofill TODO) |
| Sections | A-H (patient/insured side) | A-F (hospital side: ICD codes, procedures, pre-auth, injury, checklist, non-network) |
