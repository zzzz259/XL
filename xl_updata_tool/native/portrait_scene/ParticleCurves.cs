using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Serialization;

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class StartColor {
    public string mode { get; set; } = "";
    public float[] min { get; set; } = Array.Empty<float>();
    public float[] max { get; set; } = Array.Empty<float>();
    public float[] color_times { get; set; } = Array.Empty<float>();
    public float[][] colors { get; set; } = Array.Empty<float[]>();
    public float[] alpha_times { get; set; } = Array.Empty<float>();
    public float[] alpha { get; set; } = Array.Empty<float>();

    static void Color(float[] value) {
        if (value.Length != 4 || value.Any(v => !float.IsFinite(v) || v < 0 || v > 1)) throw new ArgumentException("invalid start color");
    }
    static void Times(float[] value) {
        if (value.Length < 2 || value.Length > 8 || value.Any(v => !float.IsFinite(v) || v < 0 || v > 1)) throw new ArgumentException("invalid gradient times");
        if (value.Zip(value.Skip(1), (a, b) => a >= b).Any(v => v)) throw new ArgumentException("gradient times are not increasing");
    }
    public void Validate() {
        if (mode == "two_colors") {
            if (min.Length != 4 || max.Length != 4) throw new ArgumentException("invalid two-color fields");
            Color(min); Color(max);
            if (color_times.Length != 0 || colors.Length != 0 || alpha_times.Length != 0 || alpha.Length != 0) throw new ArgumentException("unexpected two-color gradient fields");
            return;
        }
        if (mode != "random_fixed_gradient") throw new ArgumentException("unsupported start color mode");
        if (min.Length != 0 || max.Length != 0) throw new ArgumentException("unexpected fixed gradient constants");
        Times(color_times); Times(alpha_times);
        if (colors.Length != color_times.Length || alpha.Length != alpha_times.Length) throw new ArgumentException("gradient lengths differ");
        if (colors.Any(color => color == null || color.Length != 3 || color.Any(v => !float.IsFinite(v) || v < 0 || v > 1))) throw new ArgumentException("invalid gradient colors");
        if (alpha.Any(v => !float.IsFinite(v) || v < 0 || v > 1)) throw new ArgumentException("invalid gradient alpha");
    }
    public float[] Evaluate(long id, uint seed) {
        if (mode == "two_colors") {
            float t = ParticleLayer.Random(id, seed, 10);
            return min.Zip(max, (a, b) => a + (b - a) * t).ToArray();
        }
        float time = ParticleLayer.Random(id, seed, 11);
        int colorIndex, alphaIndex;
        ColorOverTime.Weight(color_times, time, out colorIndex);
        ColorOverTime.Weight(alpha_times, time, out alphaIndex);
        return new[]{colors[colorIndex][0],colors[colorIndex][1],colors[colorIndex][2],alpha[alphaIndex]};
    }
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class CurveKey {
    public float time { get; set; }
    public float value { get; set; }
    public float inSlope { get; set; }
    public float outSlope { get; set; }
    public int weightedMode { get; set; }
}
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class CurveData {
    public CurveKey[] keys { get; set; } = Array.Empty<CurveKey>();
    public void Validate() {
        if (keys.Length < 2 || keys[0].time != 0 || keys[^1].time != 1) throw new ArgumentException("curve must cover 0..1");
        for (int i = 0; i < keys.Length; i++) {
            var key = keys[i];
            if (!float.IsFinite(key.time) || !float.IsFinite(key.value) || float.IsNaN(key.inSlope) || float.IsNaN(key.outSlope) || key.weightedMode != 0) throw new ArgumentException("invalid curve key");
            if (i > 0 && key.time <= keys[i-1].time) throw new ArgumentException("curve times are not increasing");
        }
    }
    static void Coefficients(CurveKey lo, CurveKey hi, out float a, out float b, out float c, out float d) {
        float dt = hi.time - lo.time;
        a = 2 * lo.value - 2 * hi.value + dt * (lo.outSlope + hi.inSlope);
        b = -3 * lo.value + 3 * hi.value - dt * (2 * lo.outSlope + hi.inSlope);
        c = dt * lo.outSlope;
        d = lo.value;
    }
    public float Evaluate(float time) {
        time = Math.Clamp(time, 0, 1);
        int index = 0;
        while (index < keys.Length - 2 && time > keys[index + 1].time) index++;
        var lo = keys[index]; var hi = keys[index + 1];
        if (!float.IsFinite(lo.outSlope) || !float.IsFinite(hi.inSlope)) return lo.value;
        Coefficients(lo, hi, out float a, out float b, out float c, out float d);
        float u = (time - lo.time) / (hi.time - lo.time);
        return ((a * u + b) * u + c) * u + d;
    }
    public float Integrate(float time) {
        time = Math.Clamp(time, 0, 1);
        float total = 0;
        for (int index = 0; index + 1 < keys.Length; index++) {
            var lo = keys[index]; var hi = keys[index + 1];
            float end = Math.Min(time, hi.time);
            if (end <= lo.time) break;
            float u = (end - lo.time) / (hi.time - lo.time);
            if (!float.IsFinite(lo.outSlope) || !float.IsFinite(hi.inSlope)) {
                total += lo.value * u * (hi.time - lo.time);
            } else {
                Coefficients(lo, hi, out float a, out float b, out float c, out float d);
                total += (a * u * u * u * u / 4 + b * u * u * u / 3 + c * u * u / 2 + d * u) * (hi.time - lo.time);
            }
            if (time <= hi.time) break;
        }
        return total;
    }
    public (float min, float max) IntegralExtrema() {
        float min = 0, max = 0, total = 0;
        for (int index = 0; index + 1 < keys.Length; index++) {
            var lo = keys[index]; var hi = keys[index + 1];
            float dt = hi.time - lo.time;
            var candidates = new List<float>{0, 1};
            if (!float.IsFinite(lo.outSlope) || !float.IsFinite(hi.inSlope)) {
                min = Math.Min(min, total + Math.Min(0, lo.value * dt));
                max = Math.Max(max, total + Math.Max(0, lo.value * dt));
            } else {
                Coefficients(lo, hi, out float a, out float b, out float c, out float d);
                float Polynomial(float u) => ((a * u + b) * u + c) * u + d;
                var monotonic = new List<float>{0, 1};
                if (a != 0) {
                    float discriminant = 4 * b * b - 12 * a * c;
                    if (discriminant >= 0) {
                        float root = MathF.Sqrt(discriminant);
                        monotonic.Add((-2 * b + root) / (6 * a));
                        monotonic.Add((-2 * b - root) / (6 * a));
                    }
                } else if (b != 0) monotonic.Add(-c / (2 * b));
                var ordered = monotonic.Where(u => u >= 0 && u <= 1).Distinct().OrderBy(u => u).ToArray();
                for (int part = 0; part + 1 < ordered.Length; part++) {
                    float start = ordered[part], end = ordered[part + 1];
                    float first = Polynomial(start), last = Polynomial(end);
                    if (first == 0 || last == 0 || first < 0 && last > 0 || first > 0 && last < 0) {
                        for (int iteration = 0; iteration < 48 && end - start > .0000001f; iteration++) {
                            float middle = (start + end) / 2, value = Polynomial(middle);
                            if (value == 0) { start = end = middle; break; }
                            if (first < 0 && value > 0 || first > 0 && value < 0 || value == 0) end = middle;
                            else { start = middle; first = value; }
                        }
                        candidates.Add((start + end) / 2);
                    }
                }
                foreach (float u in candidates.Where(u => u >= 0 && u <= 1)) {
                    float value = total + (a * u * u * u * u / 4 + b * u * u * u / 3 + c * u * u / 2 + d * u) * dt;
                    min = Math.Min(min, value); max = Math.Max(max, value);
                }
            }
            total = Integrate(hi.time);
        }
        return (min, max);
    }
    public (float min, float max) Extrema() {
        float min = float.PositiveInfinity, max = float.NegativeInfinity;
        for (int index = 0; index + 1 < keys.Length; index++) {
            var lo = keys[index]; var hi = keys[index + 1];
            if (!float.IsFinite(lo.outSlope) || !float.IsFinite(hi.inSlope)) {
                min = Math.Min(min, lo.value); max = Math.Max(max, lo.value);
                continue;
            }
            Coefficients(lo, hi, out float a, out float b, out float c, out float d);
            var candidates = new List<float>{0, 1};
            if (a != 0) {
                float discriminant = 4 * b * b - 12 * a * c;
                if (discriminant >= 0) {
                    float root = MathF.Sqrt(discriminant);
                    candidates.Add((-2 * b + root) / (6 * a));
                    candidates.Add((-2 * b - root) / (6 * a));
                }
            } else if (b != 0) candidates.Add(-c / (2 * b));
            foreach (float u in candidates.Where(u => u >= 0 && u <= 1)) {
                float value = ((a * u + b) * u + c) * u + d;
                min = Math.Min(min, value); max = Math.Max(max, value);
            }
        }
        return (min, max);
    }
}
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class MinMaxCurve {
    public int mode { get; set; }
    public float scalar { get; set; }
    public float min_scalar { get; set; }
    public CurveData? max { get; set; }
    public CurveData? min { get; set; }
    public void Validate() {
        if (mode < 0 || mode > 3 || !float.IsFinite(scalar)) throw new ArgumentException("invalid MinMaxCurve");
        if (mode == 3 && !float.IsFinite(min_scalar)) throw new ArgumentException("invalid MinMaxCurve constants");
        if (mode == 1 && max == null || mode == 2 && (max == null || min == null)) throw new ArgumentException("missing MinMaxCurve curve");
        max?.Validate(); min?.Validate();
    }
    public float Evaluate(float time, long id, uint seed, uint lane) {
        time = Math.Clamp(time, 0, 1);
        if (mode == 0) return scalar;
        if (mode == 3) return min_scalar + (scalar - min_scalar) * ParticleLayer.Random(id, seed, lane);
        if (mode == 1) return max!.Evaluate(time) * scalar;
        float lo = min!.Evaluate(time), hi = max!.Evaluate(time);
        return (lo + (hi - lo) * ParticleLayer.Random(id, seed, lane)) * scalar;
    }
    public float EvaluateIntegral(float time, long id, uint seed, uint lane) {
        time = Math.Clamp(time, 0, 1);
        if (mode == 0) return scalar * time;
        if (mode == 3) return (min_scalar + (scalar - min_scalar) * ParticleLayer.Random(id, seed, lane)) * time;
        if (mode == 1) return max!.Integrate(time) * scalar;
        float lo = min!.Integrate(time), hi = max!.Integrate(time);
        return (lo + (hi - lo) * ParticleLayer.Random(id, seed, lane)) * scalar;
    }
}
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class MeshConfig {
    public string kind { get; set; } = "";
    public float[][] positions { get; set; } = Array.Empty<float[]>();
    public float[][] uvs { get; set; } = Array.Empty<float[]>();
    public int[] indices { get; set; } = Array.Empty<int>();
    public void Validate() {
        if (kind != "mesh_quad_v1" || positions.Length != 4 || uvs.Length != 4 || indices.Length != 6) throw new ArgumentException("invalid Mesh config");
        if (positions.Any(row => row.Length != 3 || row.Any(v => !float.IsFinite(v))) || uvs.Any(row => row.Length != 2 || row.Any(v => !float.IsFinite(v)))) throw new ArgumentException("invalid Mesh vertices");
        if (indices.Any(index => index < 0 || index >= 4)) throw new ArgumentException("invalid Mesh indices");
        var expectedPositions = new[]{new[]{1f,-1f,0f},new[]{1f,1f,0f},new[]{-1f,1f,0f},new[]{-1f,-1f,0f}};
        var expectedUvs = new[]{new[]{0f,0f},new[]{0f,1f},new[]{1f,1f},new[]{1f,0f}};
        if (!positions.Zip(expectedPositions, (actual, expected) => actual.SequenceEqual(expected)).All(value => value)
            || !uvs.Zip(expectedUvs, (actual, expected) => actual.SequenceEqual(expected)).All(value => value)
            || !indices.SequenceEqual(new[]{0,1,2,0,2,3})) throw new ArgumentException("Mesh differs from the evidenced low-plane geometry");
    }
}
