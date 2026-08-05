# Mixpanel Button CTA Tracking: Current vs Best Practice

## Why "Button CTA" Doesn't Show in Mixpanel

Mixpanel's **Button CTA** feature (and similar autocapture reports) comes from **Autocapture** — an automatic click-tracking feature built into the **official Mixpanel JavaScript SDK**. It only works when you use `mixpanel.init()` with `{ autocapture: true }`.

**Your app uses a custom implementation** that bypasses the official SDK entirely. So Autocapture never runs, and Mixpanel's built-in Button CTA reports have no data to show.

---

## What You're Currently Doing

### Architecture

| Aspect | Your Implementation |
|--------|----------------------|
| **Method** | Custom XHR-based implementation in `frontend/utils/Mixpanel.js` |
| **SDK** | Not using `mixpanel-browser` (it's in package.json but never imported) |
| **Event delivery** | Direct POST to `https://api.mixpanel.com/track` |
| **Platform** | Web only (React Native gets no-op stubs) |

### How It Works

1. **Custom `track()`** — You manually call `mixpanel.track(eventName, properties)` from components.
2. **Base64 encoding** — Events are encoded and sent via XHR.
3. **Manual instrumentation** — Every CTA click must be explicitly instrumented in code.

### Events You're Tracking

| Event Name | Location | Properties |
|------------|----------|------------|
| `Auth CTA Clicked` | HeaderLoginSignUp.jsx | source, platform, cta_text, user_state, portal |
| `Featured CTA Clicked` | WelcomePage.jsx | banner, source |
| `Get Help Now Clicked` | WelcomePage.jsx | source, destination |
| `Try Pill Clicked` | WelcomePage.jsx | pill_name, source |
| `Service Card Clicked` | WelcomePage.jsx | service_name, source |
| `Doctor_Book_Slot_Clicked` | DoctorsAppointmentData.jsx | doctor props, remaining_slots |
| `Doctor_Subscribe_Clicked` | DoctorsAppointmentData.jsx | doctor props, button_text |
| `Doctor_Image_Clicked` | DoctorsAppointmentData.jsx | doctor props, destination |
| `Screen Viewed` | App.jsx | screen |
| `User Logged In` | AuthContext.js | user_id, role, login_method |
| ... | ... | ... |

### Pros of Current Approach

- Works without the official SDK
- Full control over payload and batching
- Compatible with React Native (no-op on mobile)
- No Autocapture overhead or event volume

### Cons of Current Approach

- **No Autocapture** — No `[Auto] Element Click`, no Button CTA reports
- **Manual work** — Every new button needs code changes
- **Easy to miss** — New CTAs can go untracked
- **Inconsistent naming** — Events like `Get Help Now Clicked` vs `Featured CTA Clicked` vs `Doctor_Book_Slot_Clicked`

---

## Best Practice: Recommended Approaches

### Option A: Official SDK + Autocapture (Best for Button CTA)

Use the official `mixpanel-browser` SDK with Autocapture enabled. This gives you automatic Button CTA tracking with minimal code.

**Setup:**

```javascript
// frontend/utils/Mixpanel.js (or wherever you init)
import mixpanel from 'mixpanel-browser';
import { Platform } from 'react-native';

if (Platform.OS === 'web') {
  mixpanel.init('719f231a1ce17d0f0352731d53609ac3', {
    autocapture: true,        // ← Enables Button CTA / [Auto] Element Click
    track_pageview: true,
    persistence: 'localStorage',
    ignore_dnt: false,
  });
}

export default mixpanel;
```

**What you get:**

- `[Auto] Element Click` — All button/link clicks
- `[Auto] Page View` — Page views
- `[Auto] Dead Click` — Clicks with no response
- `[Auto] Rage Click` — Repeated frustrated clicks
- Built-in Button CTA reports in Mixpanel

**Trade-offs:**

- Higher event volume (more Mixpanel usage)
- Need to test in a sandbox first
- Autocapture may capture sensitive elements (use `block_selectors` or `.mp-no-track` if needed)

---

### Option B: Hybrid — Official SDK + Manual CTA Events (Recommended)

Use the official SDK for core features, keep your manual CTA events, and optionally enable Autocapture for extra coverage.

**Setup:**

```javascript
import mixpanel from 'mixpanel-browser';
import { Platform } from 'react-native';

if (Platform.OS === 'web') {
  mixpanel.init('719f231a1ce17d0f0352731d53609ac3', {
    autocapture: true,        // Automatic button tracking
    track_pageview: true,
    persistence: 'localStorage',
  });
}

// Keep your existing mixpanel.track() calls — they still work!
export default mixpanel;
```

**Benefits:**

- Button CTA reports work out of the box
- Your existing `Auth CTA Clicked`, `Featured CTA Clicked`, etc. still work
- Autocapture fills gaps for buttons you didn’t instrument
- One source of truth (official SDK)

---

### Option C: Keep Custom Implementation + Standardize CTA Events

If you must keep the custom implementation, standardize CTA tracking so you can build your own “Button CTA” reports.

**1. Use a single event name with properties:**

```javascript
// Instead of many different event names:
mixpanel.track("Button CTA Clicked", {
  cta_name: "auth",
  cta_text: "Login / Signup",
  source: "header",
  destination: "auth_modal",
  platform: Platform.OS,
});
```

**2. Create a reusable helper:**

```javascript
// utils/trackCTA.js
export const trackCTA = (ctaName, props = {}) => {
  mixpanel.track("Button CTA Clicked", {
    cta_name: ctaName,
    timestamp: new Date().toISOString(),
    ...props,
  });
};
```

**3. In Mixpanel:**

- Create an Insights report on `Button CTA Clicked`
- Break down by `cta_name`, `source`, `cta_text`
- Filter by date range

---

## Comparison Summary

| Criteria | Your Current | Option A (SDK + Autocapture) | Option B (Hybrid) | Option C (Standardize) |
|----------|--------------|------------------------------|-------------------|-------------------------|
| Button CTA in Mixpanel | ❌ No | ✅ Yes | ✅ Yes | ⚠️ Custom report |
| Manual instrumentation | Required | Optional | Optional | Required |
| Event volume | Low | Higher | Higher | Low |
| React Native support | ✅ No-op | Need conditional init | Need conditional init | ✅ No-op |
| Effort to implement | — | Low | Low | Medium |
| Maintenance | High (every button) | Low | Low | Medium |

---

## Recommendation

**Use Option B (Hybrid):** Switch to the official `mixpanel-browser` SDK with `autocapture: true`, and keep your existing `mixpanel.track()` calls. You get:

1. Button CTA reports working in Mixpanel
2. No need to remove or rewrite existing tracking
3. Autocapture for buttons you haven’t instrumented
4. Dead/Rage click insights for UX

**Migration steps:**

1. Replace the custom Mixpanel implementation with the official SDK init (web-only).
2. Add `autocapture: true` in the init config.
3. Keep all current `mixpanel.track()` calls as-is.
4. Test in a sandbox project, then roll out.
5. In Mixpanel, use both `[Auto] Element Click` and your custom events for analysis.

---

## Quick Reference: Where CTA Events Are Tracked

| File | Events |
|------|--------|
| `HeaderLoginSignUp.jsx` | Auth CTA Clicked |
| `WelcomePage.jsx` | Get Help Now, Try Pill, Service Card, Featured CTA, Mobile Menu |
| `DoctorsAppointmentData.jsx` | Doctor_Image_Clicked, Doctor_Book_Slot_Clicked, Doctor_Subscribe_Clicked |
| `AuthContext.js` | User Logged In, Signup OTP Requested, Login Failed, etc. |
| `App.jsx` | Screen Viewed, Web App Loaded |
