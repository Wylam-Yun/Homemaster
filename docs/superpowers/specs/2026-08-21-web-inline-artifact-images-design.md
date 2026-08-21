# Web Inline Artifact Images Design

## Goal

Display image artifacts directly inside the Web Console tool card that produced them, while preserving access to the original artifact. Users should no longer need to download every ALFWorld frame merely to inspect it.

## Chosen Experience

For each tool artifact whose `media_type` starts with `image/`, render a bounded thumbnail below the tool output. The thumbnail uses the existing authenticated artifact URL and keeps the filename and media type visible. Clicking the thumbnail opens an in-page lightbox with a larger contained image and an `Open original` link.

The lightbox closes through its close icon, the Escape key, or a click on the backdrop. Opening it moves focus to the close control; closing it restores focus to the thumbnail that opened it. The image `alt` text identifies the artifact filename and producing tool without claiming visual content that the application has not interpreted.

Non-image artifacts retain the existing filename link and media-type label. A failed image load shows the same artifact as a normal link so the user can retry or inspect the response directly.

## Component Boundaries

- `ToolCallCard` decides whether an artifact is an image from its projected `media_type` and renders either an image preview or the existing link row.
- A focused `ArtifactImagePreview` component owns thumbnail loading, fallback state, and opening the selected artifact.
- A focused `ImageLightbox` component owns the modal portal, keyboard handling, focus restoration, backdrop behavior, full-size image, and original-artifact link.
- The existing artifact protocol and HTTP endpoint remain authoritative. No base64 data, filesystem paths, or new public fields enter browser events.

## Data Flow

1. A completed tool event supplies the existing `ArtifactRef` with opaque handle, filename, media type, and run ID.
2. The frontend builds the existing session- and run-scoped `/api/artifacts/{handle}` URL once and passes it to both thumbnail and lightbox.
3. The browser fetches image bytes from the existing endpoint, which continues enforcing session, run, handle, quota, and expiry rules.
4. Selecting a thumbnail stores that immutable artifact reference as the active lightbox item. Closing clears it without changing conversation or tool state.

## Layout

The thumbnail occupies the tool card width with a stable `16 / 9` preview area, uses `object-fit: contain`, and never stretches the source image. The preview remains within the existing tool card rather than becoming a separate chat message, keeping each observation next to the action that produced it.

The lightbox uses a dark backdrop and a viewport-bounded image. Controls do not overlap the image content: the close icon sits in a dedicated top-right control area, while filename, media type, and `Open original` sit in a footer. Mobile uses the same structure with reduced outer padding.

## Error And Security Behavior

- Only `image/*` media types receive an `<img>` preview; all other values use the existing download link.
- A thumbnail or lightbox load error falls back to a visible link and does not fail the tool card or conversation.
- URLs remain same-origin and contain only the existing opaque handle plus session and run identifiers.
- The frontend does not bypass artifact authorization, cache raw bytes, infer local paths, or expose internal artifact metadata.
- Expired, missing, or unauthorized artifacts retain the backend HTTP failure and remain visibly recoverable as links.

## Verification

- Component tests cover image preview rendering, non-image link preservation, accessible labels, click-to-open, close button, Escape, backdrop close, focus restoration, and image-load fallback.
- Tests assert that both thumbnail and original link use the exact existing session/run-scoped URL.
- Frontend typecheck, unit tests, and production build must pass.
- A browser black-box run on desktop and mobile must show a real ALFWorld PNG inside its tool card, open a nonblank enlarged image, close by all three mechanisms, and leave surrounding controls unobscured.
- The artifact endpoint response must remain HTTP 200 for the authorized image, and the decoded image dimensions and nonblank pixels must be checked independently of DOM visibility.

## Non-Goals

- No gallery, carousel, annotation, image editing, zoom/pan engine, or eager preloading.
- No backend protocol or persistence change.
- No inline rendering for video, audio, PDF, or arbitrary text artifacts.
