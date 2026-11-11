# Static PNG working memory

Schema2 PNG descriptors may set `settings.png_working_rows` to1..1024. Zero (the default) preserves the existing in-memory path. Schema1, JPEG and video cannot enable this option.

The full render target/view and pixel math remain unchanged. An exclusive adjacent float32 store retains destination pixels while a bounded row window composes captures. GL readback uses one row from the full target. Scene-local textures share only when encoded content and all sampler/loader policies match; atlases release before the scene owner frees those uploads.

On Linux the working directory must be disk-backed. tmpfs/ramfs are rejected before sizing the file. The file is unlinked after sizing, before writing pixel data; the live file descriptor owns it, so a terminated process does not leave a large named float store. Completed windows are fsynced and advised away using checked `posix_fadvise`. Managed cleanup remains necessary on other platforms.

The canvas estimate is an allocation screen, not a process-tree budget. Run preparation/rendering under an external CPU affinity, memory/swap limit and timeout. Include source textures, native/driver memory and file cache in measurements. Never reduce scale/effects to make a task pass silently. Inputs must remain immutable during a render.

`tests/native/ParticleColorCheck.cs` covers color/alpha gradients, exact material blending, independent XY sizes, circle-edge emission, fractional UV frame dimensions, parent rotation and legacy pixels. `tests/native/DiskCanvasCheck.cs` covers exact bytes/Peak, partial windows, PMA, blend equations, cancellation, owned-file collisions and disk checks; its `--graphics` mode compares GL row readback with CopyToImage. `TextureCacheCheck.cs` covers content/policy identities and optionally nine-version GPU sharing/lifetimes. Compile these with the same references/source glob used by `scripts/build-portrait-scene-renderer.py`, selecting the corresponding class with `-main:`. Run the resulting assembly with one owned work-directory argument; GPU tests additionally require `--graphics` and a valid private display. No pytest entry is required.

Real-game acceptance additionally requires full decoded RGBA/metadata comparison and cache-inclusive whole-tree resource measurements; a tiny synthetic smoke cannot substitute for that evidence.

The Linux `libSkiaSharp.so` shipped with the CLI comes from the official `SkiaSharp.NativeAssets.Linux.NoDependencies` 3.119.0 NuGet package (`https://api.nuget.org/v3-flatcontainer/skiasharp.nativeassets.linux.nodependencies/3.119.0/skiasharp.nativeassets.linux.nodependencies.3.119.0.nupkg`). Its SHA-256 is `3731e7f453face336de3a60e3d6753511ce1d8a5d17c2952f9f964c42d589248`; it matches the bundled SkiaSharp 3.119.0 managed assembly.
