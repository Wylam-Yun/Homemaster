# Web Inline Artifact Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render authorized image artifacts inside their producing Web tool card and open them in an accessible in-page lightbox.

**Architecture:** Keep the existing `ArtifactRef` and `/api/artifacts/{handle}` contract unchanged. `ToolCallCard` builds the existing session/run-scoped URL and delegates image rendering to `ArtifactImagePreview`; `ImageLightbox` owns portal, keyboard, backdrop, focus, and body-scroll behavior. Non-image and failed-image artifacts retain the existing link presentation.

**Tech Stack:** React 18, TypeScript 6, CSS modules, React DOM portals, Vitest, Testing Library, Vite, existing FastAPI artifact endpoint.

---

## File Structure

- Create `web/src/components/ArtifactImagePreview.tsx`: thumbnail state, load fallback, lightbox selection.
- Create `web/src/components/ArtifactImagePreview.module.css`: stable thumbnail and metadata layout.
- Create `web/src/components/ImageLightbox.tsx`: modal portal, Escape/backdrop/close behavior, focus restoration, full-image fallback.
- Create `web/src/components/ImageLightbox.module.css`: viewport-bounded modal layout on desktop and mobile.
- Modify `web/src/components/ToolCallCard.tsx`: build one authorized artifact URL and route `image/*` refs to the preview.
- Modify `web/src/components/ToolCallCard.module.css`: keep non-image artifact rows stable after image rows are introduced.
- Modify `web/src/components/components.test.tsx`: component behavior and exact URL coverage.
- Modify `README.md`: include inline artifact images in the Web Console capability summary.
- Modify `docs/web-console-user-guide.md`: explain preview, enlargement, closing, original link, and fallback.
- Modify `CHANGELOG.md`: record the user-visible capability and external verification.
- Rebuild `src/homemaster/web/static_dist/`: ship the new React production bundle.

### Task 1: Lock The Component Contract With Failing Tests

**Files:**
- Modify: `web/src/components/components.test.tsx`

- [ ] **Step 1: Add exact image and non-image fixtures**

Add imports and fixture helpers so every assertion uses one opaque handle and the exact session/run-scoped URL:

```tsx
import type { ArtifactRef } from '../protocol/events'

const imageArtifact: ArtifactRef = {
  artifact_handle: `hm-artifact:${'a'.repeat(32)}`,
  run_id: 'run-01',
  filename: 'frame-0004.png',
  media_type: 'image/png',
  content_sha256: 'b'.repeat(64),
}

const imageUrl = `/api/artifacts/${encodeURIComponent(imageArtifact.artifact_handle)}?session_id=session-01&run_id=run-01`
```

- [ ] **Step 2: Add the failing preview/lightbox test**

```tsx
it('previews image artifacts and opens an accessible lightbox with the same URL', () => {
  render(<ToolCallCard sessionId="session-01" tool={{
    toolCallId: 'call-01',
    name: 'robot_manipulate',
    arguments: {},
    status: 'completed',
    output: 'Action completed.',
    artifacts: [imageArtifact],
  }} />)

  const trigger = screen.getByRole('button', { name: 'Enlarge frame-0004.png' })
  expect(screen.getByRole('img', { name: 'frame-0004.png from robot_manipulate' })).toHaveAttribute('src', imageUrl)
  fireEvent.click(trigger)

  const dialog = screen.getByRole('dialog', { name: 'Image preview: frame-0004.png' })
  expect(dialog).toBeVisible()
  expect(screen.getByRole('button', { name: 'Close image preview' })).toHaveFocus()
  expect(screen.getByRole('link', { name: 'Open original' })).toHaveAttribute('href', imageUrl)

  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(trigger).toHaveFocus()
})
```

- [ ] **Step 3: Add failing close, fallback, and non-image tests**

Add tests that reopen the dialog and click its backdrop, click the close button, fire `error` on the thumbnail and full image, and assert each failure leaves a filename link using `imageUrl`. Preserve a `text/plain` fixture assertion that no image or enlarge button is rendered and its existing artifact link remains exact.

- [ ] **Step 4: Run the focused test and verify the red state**

Run:

```bash
cd web
npm test -- --run src/components/components.test.tsx
```

Expected: FAIL because no `Enlarge frame-0004.png` button or image-preview dialog exists.

### Task 2: Implement Preview And Lightbox Components

**Files:**
- Create: `web/src/components/ArtifactImagePreview.tsx`
- Create: `web/src/components/ArtifactImagePreview.module.css`
- Create: `web/src/components/ImageLightbox.tsx`
- Create: `web/src/components/ImageLightbox.module.css`
- Modify: `web/src/components/ToolCallCard.tsx`
- Modify: `web/src/components/ToolCallCard.module.css`
- Test: `web/src/components/components.test.tsx`

- [ ] **Step 1: Implement the lightbox lifecycle**

Create `ImageLightbox` with props `{ filename, mediaType, toolName, url, onClose }`. Use `createPortal(..., document.body)`, `useId()` for `aria-labelledby`, and a close-button ref. On mount, save `document.activeElement`, focus the close button, set `document.body.style.overflow = 'hidden'`, and register Escape. Cleanup restores overflow and focus. Backdrop close must require `event.target === event.currentTarget`.

The rendered structure is:

```tsx
<div className={styles.backdrop} role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={onBackdropClick}>
  <section className={styles.dialog}>
    <header>
      <div><h2 id={titleId}>Image preview: {filename}</h2><small>{mediaType}</small></div>
      <button ref={closeRef} type="button" aria-label="Close image preview" onClick={onClose}>×</button>
    </header>
    {failed
      ? <p className={styles.failure}>Preview unavailable. <a href={url}>Open {filename}</a></p>
      : <img src={url} alt={`${filename} from ${toolName}`} onError={() => setFailed(true)} />}
    <footer><span>{filename}</span><a href={url} target="_blank" rel="noreferrer">Open original</a></footer>
  </section>
</div>
```

- [ ] **Step 2: Implement the thumbnail and fallback**

Create `ArtifactImagePreview` with props `{ artifact, toolName, url }`. Before an error it renders a list item containing a `button` named `Enlarge ${artifact.filename}`, a contained `<img>`, and filename/media-type metadata. Clicking opens `ImageLightbox`. After thumbnail `onError`, replace the preview with the same filename/media-type link row used for non-image artifacts.

- [ ] **Step 3: Integrate through the existing artifact mapping**

In `ToolCallCard`, calculate the URL once per artifact:

```tsx
const artifactUrl = (artifact: ArtifactRef, sessionId: string): string => (
  `/api/artifacts/${encodeURIComponent(artifact.artifact_handle)}`
  + `?session_id=${encodeURIComponent(sessionId)}`
  + `&run_id=${encodeURIComponent(artifact.run_id)}`
)
```

Render `ArtifactImagePreview` only when `sessionId !== undefined && artifact.media_type.startsWith('image/')`. Preserve the current span fallback when no session ID exists and the current anchor for every non-image artifact.

- [ ] **Step 4: Add stable responsive CSS**

Use an `aspect-ratio: 16 / 9` thumbnail button, `object-fit: contain`, 8px-or-smaller radii, and no fixed font scaling. The lightbox is `position: fixed`, fills the viewport, constrains the dialog to `min(64rem, calc(100vw - 2rem))`, constrains the image to available viewport height, and reduces padding below 720px. Its header/footer occupy their own rows so controls never overlap the image.

- [ ] **Step 5: Run focused tests to green**

Run:

```bash
cd web
npm test -- --run src/components/components.test.tsx
```

Expected: all component tests pass, including preview, exact URL, three close mechanisms, focus restoration, both image errors, and non-image preservation.

### Task 3: Complete Regression, Documentation, And Production Packaging

**Files:**
- Modify: `README.md`
- Modify: `docs/web-console-user-guide.md`
- Modify: `CHANGELOG.md`
- Rebuild: `src/homemaster/web/static_dist/index.html`
- Rebuild: `src/homemaster/web/static_dist/assets/*`

- [ ] **Step 1: Run the complete frontend gates**

Run:

```bash
cd web
npm test
npm run typecheck
npm run build
```

Expected: all Vitest tests pass, TypeScript exits 0, and Vite writes a new hashed JS/CSS pair to `src/homemaster/web/static_dist/assets/` without retaining stale bundle files.

- [ ] **Step 2: Run the Python Web regression**

Run:

```bash
.runtime/venv/bin/python -m pytest -q tests/homemaster/web
.runtime/venv/bin/python -m ruff check src/homemaster/web tests/homemaster/web
git diff --check
```

Expected: all Web tests pass, Ruff passes, and the diff has no whitespace errors.

- [ ] **Step 3: Update user-facing documentation**

Update the README Web Console capability line and the user-guide artifact bullet to state that `image/*` artifacts render inline, click to enlarge, close through the icon/Escape/backdrop, retain `Open original`, and fall back to the authorized link. Add the same behavioral summary and verification result under `CHANGELOG.md` Unreleased.

- [ ] **Step 4: Verify a real artifact and browser terminal state**

Start `homemaster serve --alfworld` with a fresh fixed episode, complete at least one approved visual tool action, and require the artifact endpoint to return HTTP 200. Independently decode the PNG and assert width and height are positive and its pixel extrema are nonblank. In headless `/usr/bin/google-chrome`, load the Web Console, capture desktop and mobile screenshots, click the real thumbnail, assert the dialog and full image are visible, close by Escape/backdrop/button in separate openings, and assert composer/approval controls are not obscured after close.

- [ ] **Step 5: Commit the complete feature**

Stage only the implementation, tests, rebuilt static bundle, README, user guide, and CHANGELOG. Preserve unrelated `story/` files. Commit with a message whose body matches the CHANGELOG capability and verification statement:

```bash
git commit -m "feat(web): preview image artifacts inline" \
  -m "Render authorized image artifacts inside their producing tool card, open them in an accessible lightbox, preserve the original-artifact link and non-image behavior, and fall back to the authorized link on image errors. Frontend/Python regression and real ALFWorld image terminal checks pass."
```
