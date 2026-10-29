// Scene compositor built against the unmodified bundled Spine/SFML runtime.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using SFML.Graphics;
using SFML.System;
using Spine;
using Spine.Exporters;
using Spine.Implementations;
using Spine.Interfaces;
using SkiaSharp;

sealed class Step { public string animation { get; set; } = ""; public bool loop { get; set; } }
sealed class Track { public int track { get; set; } public Step[] sequence { get; set; } = Array.Empty<Step>(); }
sealed class Part {
    public string kind { get; set; } = "spine";
    public string texture { get; set; } = "";
    public JsonElement unity_order { get; set; }
    public double[] Order => unity_order.ValueKind == JsonValueKind.Array ? unity_order.EnumerateArray().Select(v => v.GetDouble()).ToArray() : new[]{0d, unity_order.ValueKind == JsonValueKind.Number ? unity_order.GetDouble() : 0d, 0d, 0d};
    public ParticleConfig? particle { get; set; }
    public Track[] tracks { get; set; } = Array.Empty<Track>();
    public string id { get; set; } = "";
    public string skeleton { get; set; } = "";
    public string atlas { get; set; } = "";
    public string animation { get; set; } = "";
    public bool loop { get; set; } = true;
    public string skin { get; set; } = "default";
    public float time_scale { get; set; } = 1;
    public Step[] sequence { get; set; } = Array.Empty<Step>();
    public float[] affine { get; set; } = { 1, 0, 0, 1, 0, 0 };
    public float[] color { get; set; } = { 1, 1, 1, 1 };
    public float fade_in { get; set; }
}
sealed class Settings {
    public string format { get; set; } = "Png";
    [JsonPropertyName("static")] public bool Static { get; set; } = true;
    public float scale { get; set; } = 1;
    public int max_resolution { get; set; } = 2048;
    public int margin { get; set; }
    public bool transparent { get; set; } = true;
    public bool pma { get; set; }
    public float fps { get; set; } = 30;
    public string physics { get; set; } = "Update";
    public float warm_up { get; set; }
    public float time_offset { get; set; }
    public float? duration { get; set; }
    public float speed { get; set; } = 1;
    public bool loop { get; set; }
    public bool disable_track_loop { get; set; }
    public string[] skins { get; set; } = Array.Empty<string>();
    public string background_color { get; set; } = "#00000000";
    public bool auto_border { get; set; } = true;
}
sealed class Scene {
    public int scene_schema_version { get; set; }
    public Part[] parts { get; set; } = Array.Empty<Part>();
    public string output { get; set; } = "";
    public string snapshot_policy { get; set; } = "time_offset";
    public Settings settings { get; set; } = new();
}
// A non-rendering copy supplies elapsed scene time even when a part's own clock
// is frozen. The vendor exporter advances all SpineObjects before drawing.
sealed class SceneClock : SpineObject {
    public SceneClock(SpineObject source) : base(source, false) { }
    public float Elapsed => AnimationState.GetCurrent(0)?.TrackTime ?? 0;
    public override void Draw(RenderTarget target, RenderStates states) { }
}
// Update is nonvirtual in the vendor API. Export a lightweight non-rendering
// copy, then advance the real object in Draw using the shared scene clock.
// This keeps skeleton physics on the same per-part clock as animation state.
sealed class SceneProxy : SpineObject {
    readonly ScenePart source;
    public SceneProxy(ScenePart source) : base(source, false) { this.source = source; }
    public override void Draw(RenderTarget target, RenderStates states) {
        source.Synchronize();
        source.Draw(target, states);
    }
}
sealed partial class ScenePart : SpineObject {
    public readonly Part Spec;
    public SceneClock? Clock;
    readonly Transform affine;
    float elapsed;
    public float ClockTime => elapsed * Spec.time_scale;
    public ScenePart(Part spec, Settings settings) : base(spec.skeleton, spec.atlas, SpineVersion.GetVersion(spec.skeleton), TextureLoader.DefaultLoader) {
        Spec = spec;
        var m = spec.affine;
        // Bundled DrawTexture submits raw Spine Y-up vertices to SFML.
        // Apply D*A (D=diag(1,-1)) once, to both drawing and bounds.
        affine = new Transform(m[0], m[1], m[4], -m[2], -m[3], -m[5], 0, 0, 1);
        UsePma = settings.pma;
        Physics = Enum.Parse<ISkeleton.Physics>(settings.physics, true);
        if (!string.IsNullOrEmpty(spec.skin)) {
            if (!Data.SkinsByName.TryGetValue(spec.skin, out var selectedSkin)) throw new ArgumentException($"Part {spec.id}: missing skin {spec.skin}");
            Skeleton.Skin = selectedSkin;
            Skeleton.SetSlotsToSetupPose(); Skeleton.UpdateCache();
        }
        Data.DefaultMix = 0;
        Reset(settings);
    }
    public Step[] Steps => Spec.sequence.Length > 0 ? Spec.sequence : new[] { new Step { animation = Spec.animation, loop = Spec.loop } };
    public void Reset(Settings settings) {
        elapsed = 0;
        AnimationState.ClearTracks();
        Skeleton.SetToSetupPose();
        AnimationState.TimeScale = Spec.time_scale;
        var steps = Steps;
        for (int i = 0; i < steps.Length; ++i) {
            if (!Data.AnimationsByName.ContainsKey(steps[i].animation)) throw new ArgumentException($"Part {Spec.id}: missing animation {steps[i].animation}");
            bool loop = steps[i].loop && !settings.disable_track_loop;
            if (i == 0) AnimationState.SetAnimation(0, steps[i].animation, loop);
            else AnimationState.AddAnimation(0, steps[i].animation, loop, 0);
        }
        if (Spec.tracks.Select(t => t.track).Distinct().Count() != Spec.tracks.Length) throw new ArgumentException("Duplicate track index");
        foreach (var track in Spec.tracks) {
            if (track.track < 0 || track.sequence.Length == 0) throw new ArgumentException("Invalid track sequence");
            for (int i = 0; i < track.sequence.Length; i++) {
                var item = track.sequence[i];
                if (!Data.AnimationsByName.ContainsKey(item.animation)) throw new ArgumentException($"Part {Spec.id}: missing animation {item.animation}");
                if (i == 0) AnimationState.SetAnimation(track.track, item.animation, item.loop && !settings.disable_track_loop);
                else AnimationState.AddAnimation(track.track, item.animation, item.loop && !settings.disable_track_loop, 0);
            }
        }
        Skeleton.R = Spec.color[0]; Skeleton.G = Spec.color[1]; Skeleton.B = Spec.color[2]; Skeleton.A = Spec.color[3];
        Skeleton.UpdateWorldTransform(ISkeleton.Physics.Reset);
        Update(0);
    }
    public void Advance(float delta) {
        AnimationState.Update(delta);
        AnimationState.Apply(Skeleton);
        Skeleton.Update(delta * Spec.time_scale);
        Skeleton.UpdateWorldTransform(Physics);
        elapsed += delta;
    }
    public void Synchronize() {
        float remaining = Math.Max(0, (Clock?.Elapsed ?? elapsed) - elapsed);
        while (remaining > 0) {
            float delta = Math.Min(remaining, 1f / 60);
            Advance(delta);
            remaining -= delta;
        }
        Advance(0);
    }
    public float TimelineDuration(bool introOnly = false) {
        if (Spec.time_scale == 0) return Spec.fade_in;
        var steps = Spec.tracks.FirstOrDefault(t => t.track == 0)?.sequence ?? Steps;
        int count = introOnly ? Math.Max(0, steps.Length - 1) : steps.Length;
        float duration = steps.Take(count).Sum(step => Data.AnimationsByName[step.animation].Duration) / Spec.time_scale;
        // Step just past queued boundaries: float accumulation otherwise leaves
        // an entrance frame on its final tick instead of entering the idle track.
        if (introOnly && count > 0) duration += Math.Max(0.0001f, duration * 0.00001f);
        foreach (var track in Spec.tracks) {
            int n = introOnly ? Math.Max(0, track.sequence.Length - 1) : track.sequence.Length;
            duration = Math.Max(duration, track.sequence.Take(n).Sum(item => Data.AnimationsByName[item.animation].Duration) / Spec.time_scale);
        }
        return Math.Max(Spec.fade_in, duration);
    }
    public float Fade => Spec.fade_in <= 0 ? 1 : Math.Clamp((Clock?.Elapsed ?? elapsed) / Spec.fade_in, 0, 1);
    public override void Draw(RenderTarget target, RenderStates states) {
        float alpha = Skeleton.A;
        Skeleton.A = alpha * Fade;
        states.Transform *= affine;
        try { base.Draw(target, states); }
        finally { Skeleton.A = alpha; }
    }
    public void IncludeBounds(ref float left, ref float top, ref float right, ref float bottom) {
        Skeleton.GetBounds(out float x, out float y, out float width, out float height);
        foreach (float px in new[] { x, x + width }) foreach (float py in new[] { y, y + height }) {
            var point = affine.TransformPoint(px, py);
            if (!float.IsFinite(point.X) || !float.IsFinite(point.Y)) throw new ArgumentException($"Part {Spec.id}: nonfinite bounds");
            left = Math.Min(left, point.X); right = Math.Max(right, point.X);
            top = Math.Min(top, point.Y); bottom = Math.Max(bottom, point.Y);
        }
    }
}
static class Program {
    static readonly CancellationTokenSource Cancellation = new();
    static void Log(object value) => Console.WriteLine(JsonSerializer.Serialize(value));
    static void Require(bool valid, string message) { if (!valid) throw new ArgumentException(message); }
    static bool Finite(float value) => float.IsFinite(value);
    static void Validate(Scene scene) {
        Require(scene.scene_schema_version is 1 or 2, "scene_schema_version must be 1 or 2");
        Require(scene.parts.Length > 0, "Scene has no parts");
        Require(!string.IsNullOrWhiteSpace(scene.output), "Scene output is missing");
        Require(scene.snapshot_policy is "time_offset" or "entrance_complete", "Unknown snapshot_policy");
        var s = scene.settings;
        Require(Finite(s.scale) && s.scale > 0, "scale must be finite and positive");
        Require(Finite(s.speed) && s.speed > 0, "speed must be finite and positive");
        Require(Finite(s.fps) && s.fps > 0 && s.fps <= 1000, "fps must be in (0,1000]");
        Require(s.max_resolution > 0 && s.margin >= 0 && 2L * s.margin < s.max_resolution, "Invalid max_resolution/margin");
        Require(Finite(s.time_offset) && s.time_offset >= 0 && Finite(s.warm_up), "Invalid time_offset/warm_up");
        Require(!s.duration.HasValue || Finite(s.duration.Value), "duration must be finite");
        foreach (var p in scene.parts) {
            Require(p.unity_order.ValueKind is JsonValueKind.Undefined or JsonValueKind.Number or JsonValueKind.Array, "Invalid unity_order");
            Require(p.Order.Length == 4 && p.Order.All(double.IsFinite), "unity_order must have four finite components");
            Require(p.kind is "spine" or "particle", $"Part {p.id}: unsupported kind");
            if (p.kind == "particle") { Require(scene.scene_schema_version == 2, "Particles require descriptor2"); ParticleLayer.Validate(p); }
            else Require(File.Exists(p.skeleton) && File.Exists(p.atlas), $"Part {p.id}: skeleton/atlas file missing");
            Require(Finite(p.time_scale) && p.time_scale >= 0, $"Part {p.id}: time_scale must be finite and nonnegative");
            Require(Finite(p.fade_in) && p.fade_in >= 0, $"Part {p.id}: invalid fade_in");
            Require(p.affine.Length == 6 && p.affine.All(Finite), $"Part {p.id}: affine must contain six finite numbers");
            Require(Math.Abs((double)p.affine[0] * p.affine[3] - (double)p.affine[1] * p.affine[2]) > 1e-12, $"Part {p.id}: degenerate affine");
            Require(p.color.Length == 4 && p.color.All(v => Finite(v) && v >= 0 && v <= 1), $"Part {p.id}: invalid color");
        }
    }
    static void Advance(IEnumerable<SpineObject> objects, float duration, float fps) {
        float step = Math.Min(1f / fps, 1f / 60);
        for (double t = 0; t < duration;) {
            Cancellation.Token.ThrowIfCancellationRequested();
            float dt = (float)Math.Min(step, duration - t);
            foreach (var part in objects) {
                if (part is ScenePart scenePart) scenePart.Advance(dt);
                else part.Update(dt);
            }
            t += dt;
        }
        foreach (var part in objects) if (part is ScenePart scenePart) scenePart.Advance(0);
    }
    static Color Background(Settings settings) {
        if (settings.transparent) return Color.Transparent;
        var text = settings.background_color.TrimStart('#');
        Require(text.Length is 6 or 8, "background_color must be #RRGGBB or #RRGGBBAA");
        byte r = Convert.ToByte(text.Substring(0, 2), 16), g = Convert.ToByte(text.Substring(2, 2), 16), b = Convert.ToByte(text.Substring(4, 2), 16);
        byte a = text.Length == 8 ? Convert.ToByte(text.Substring(6, 2), 16) : (byte)255;
        return new Color(r, g, b, a);
    }
    static int Main(string[] args) {
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; Cancellation.Cancel(); };
        // Optional cooperative cancellation for pipe-based worker hosts.
        if (Console.IsInputRedirected) _ = Task.Run(() => {
            while (Console.ReadLine() is string line) if (line.Trim() == "cancel") Cancellation.Cancel();
        });
        string? staged = null;
        var parts = new List<ScenePart>();
        var proxies = new List<SceneProxy>();
        SceneClock? clock = null;
        try {
            if(args.Length is 3 or 4 && args[0]=="--check-canvas") {
                int canvasWidth=int.Parse(args[1]),canvasHeight=int.Parse(args[2]);
                long estimated=FramePipeline.ValidateCanvasDimensions(canvasWidth,canvasHeight,args.Length==4?long.Parse(args[3]):null);
                Log(new {phase="canvas_ready",width=canvasWidth,height=canvasHeight,pixels=(long)canvasWidth*canvasHeight,max_pixels=FramePipeline.MaxCanvasPixels,estimated_bytes=estimated});return 0;
            }
            Require(args.Length == 1, "Usage: dotnet PortraitSceneCLI.dll <descriptor.json>");
            var scene = JsonSerializer.Deserialize<Scene>(File.ReadAllText(args[0])) ?? throw new ArgumentException("Empty descriptor");
            Validate(scene);
            if (scene.scene_schema_version == 2) return FramePipeline.Run(scene, Cancellation.Token);
            var s = scene.settings;
            Log(new { phase = "load", parts = scene.parts.Length });
            foreach (var spec in scene.parts) {
                Cancellation.Token.ThrowIfCancellationRequested();
                var part = new ScenePart(spec, s); parts.Add(part);
                Log(new { phase = "part", id = spec.id, time_scale = spec.time_scale, sequence = part.Steps, affine = spec.affine });
            }
            clock = new SceneClock(parts[0]);
            string clockAnimation = parts[0].Steps[0].animation;
            void ResetClock() { clock.AnimationState.ClearTracks(); clock.AnimationState.TimeScale = 1; clock.AnimationState.SetAnimation(0, clockAnimation, true); clock.Update(0); }
            ResetClock();
            foreach (var part in parts) part.Clock = clock;
            SpineObject[] objects = parts.Cast<SpineObject>().Append(clock).ToArray();
            float natural = parts.Max(p => p.TimelineDuration());
            float snapshot = scene.snapshot_policy == "entrance_complete" ? Math.Max(s.time_offset, parts.Max(p => p.TimelineDuration(true))) : s.time_offset;
            bool still = s.format.Equals("Png", StringComparison.OrdinalIgnoreCase) || s.format.Equals("Jpg", StringComparison.OrdinalIgnoreCase);
            float duration = s.duration.HasValue && s.duration.Value >= 0 ? s.duration.Value : Math.Max(natural / s.speed, 1 / s.fps);
            float warm = s.warm_up < 0 ? natural * -s.warm_up : s.warm_up;
            // Bounds probing is discarded: animation and physics are reset before export.
            Log(new { phase = "bounds", duration, snapshot_time = snapshot });
            Advance(objects, warm, s.fps);
            Advance(objects, still ? snapshot : s.time_offset, s.fps);
            float left = float.PositiveInfinity, top = float.PositiveInfinity, right = float.NegativeInfinity, bottom = float.NegativeInfinity;
            void Bounds() { foreach (var p in parts) p.IncludeBounds(ref left, ref top, ref right, ref bottom); }
            Bounds();
            if (!still) {
                double timeline = duration * s.speed;
                double step = Math.Min(1f / s.fps * s.speed, 1f / 60);
                for (double t = 0; t < timeline;) { float dt = (float)Math.Min(step, timeline - t); Advance(objects, dt, s.fps); t += dt; Bounds(); }
            }
            Require(Finite(left) && right > left && bottom > top, "Scene has empty or invalid bounds");
            float width = right - left, height = bottom - top;
            float fit = Math.Min(s.scale, (s.max_resolution - s.margin * 2) / Math.Max(width, height));
            uint w = (uint)Math.Max(1, Math.Ceiling(width * fit) + s.margin * 2), h = (uint)Math.Max(1, Math.Ceiling(height * fit) + s.margin * 2);
            if (!still && s.format.Equals("Mp4", StringComparison.OrdinalIgnoreCase)) { w += w % 2; h += h % 2; }
            foreach (var p in parts) p.Reset(s); ResetClock();
            Advance(objects, warm, s.fps);
            Advance(objects, still ? snapshot : s.time_offset, s.fps);
            string output = Path.GetFullPath(scene.output);
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            staged = Path.Combine(Path.GetDirectoryName(output)!, "." + Path.GetFileNameWithoutExtension(output) + "." + Guid.NewGuid().ToString("N") + ".partial" + Path.GetExtension(output));
            using BaseExporter exporter = still ? new FrameExporter(w, h) { Format = s.format.Equals("Jpg", StringComparison.OrdinalIgnoreCase) ? SKEncodedImageFormat.Jpeg : SKEncodedImageFormat.Png } : new FFmpegVideoExporter(w, h) { Format = Enum.Parse<FFmpegVideoExporter.VideoFormat>(s.format, true), Duration = duration, Fps = s.fps, Speed = s.speed, Loop = s.loop, KeepLast = true };
            exporter.Center = new Vector2f((left + right) / 2, (top + bottom) / 2);
            exporter.Size = new Vector2f(w / fit, h / fit);
            exporter.BackgroundColor = Background(s);
            float lastProgress = -s.fps;
            exporter.ProgressReporter = (total, current, message) => {
                Cancellation.Token.ThrowIfCancellationRequested();
                if (current - lastProgress >= s.fps || current >= total) { Log(new { phase = "export", total, current }); lastProgress = current; }
            };
            Log(new { phase = "export_start", width = w, height = h, duration });
            foreach (var p in parts) proxies.Add(new SceneProxy(p));
            SpineObject[] exportObjects = proxies.Cast<SpineObject>().Append(clock).ToArray();
            if (exporter is FFmpegVideoExporter video) video.Export(staged, Cancellation.Token, exportObjects); else exporter.Export(staged, exportObjects);
            Cancellation.Token.ThrowIfCancellationRequested();
            Require(File.Exists(staged) && new FileInfo(staged).Length > 0, "Exporter produced no output; check ffmpeg/runtime availability");
            File.Move(staged, output, true); staged = null;
            Log(new { phase = "complete", output, width = w, height = h, duration = still ? 0 : duration, snapshot_time = snapshot, bounds = new[] { left, top, right, bottom }, parts = parts.Select(p => new { id = p.Spec.id, animation = p.AnimationState.GetCurrent(0)?.Animation.Name, track_time = p.AnimationState.GetCurrent(0)?.TrackTime ?? 0, clock_time = p.ClockTime, alpha = p.Skeleton.A * p.Fade }) });
            return 0;
        } catch (OperationCanceledException) { Log(new { phase = "cancelled" }); return 130; }
        catch (JsonException e) { Log(new { phase = "error", error = "Unsupported or unknown descriptor field: " + e.Message }); return 1; }
        catch (Exception e) { Log(new { phase = "error", error = e.ToString() }); return 1; }
        finally { if (staged != null && File.Exists(staged)) File.Delete(staged); foreach (var proxy in proxies) proxy.Dispose(); clock?.Dispose(); foreach (var part in parts) part.Dispose(); }
    }
}
