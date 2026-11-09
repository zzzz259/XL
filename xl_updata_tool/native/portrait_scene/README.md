# Static PNG working memory

Schema2 PNG descriptors may set `settings.png_working_rows` to1..1024. Zero (the default) preserves the existing in-memory path. Schema1, JPEG and video cannot enable this option.

The full render target/view and pixel math remain unchanged. An exclusive adjacent float32 store retains destination pixels while a bounded row window composes captures. GL readback uses one row from the full target. Scene-local textures share only when encoded content and all sampler/loader policies match; atlases release before the scene owner frees those uploads.

On Linux the working directory must be disk-backed. tmpfs/ramfs are rejected before sizing the file. The file is unlinked after sizing, before writing pixel data; the live file descriptor owns it, so a terminated process does not leave a large named float store. Completed windows are fsynced and advised away using checked `posix_fadvise`. Managed cleanup remains necessary on other platforms.

The canvas estimate is an allocation screen, not a process-tree budget. Run preparation/rendering under an external CPU affinity, memory/swap limit and timeout. Include source textures, native/driver memory and file cache in measurements. Never reduce scale/effects to make a task pass silently. Inputs must remain immutable during a render.

`tests/native/DiskCanvasCheck.cs` covers exact bytes/Peak, partial windows, PMA, blend equations, cancellation, owned-file collisions and disk checks; its `--graphics` mode compares GL row readback with CopyToImage. `TextureCacheCheck.cs` covers content/policy identities and optionally nine-version GPU sharing/lifetimes. Compile these with the same references/source glob used by `scripts/build-portrait-scene-renderer.py`, selecting the corresponding class with `-main:`. Run the resulting assembly with one owned work-directory argument; GPU tests additionally require `--graphics` and a valid private display. No pytest entry is required.

Real-game acceptance additionally requires full decoded RGBA/metadata comparison and cache-inclusive whole-tree resource measurements; a tiny synthetic smoke cannot substitute for that evidence.
