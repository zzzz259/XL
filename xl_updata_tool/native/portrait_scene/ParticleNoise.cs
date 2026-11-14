using System;
using System.Linq;
using System.Text.Json.Serialization;

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class NoiseConfig {
    public float strength { get; set; }
    public float frequency { get; set; }
    public bool damping { get; set; }
    public float position_amount { get; set; }
    public void Validate() {
        if (!float.IsFinite(strength) || strength < 0
            || !float.IsFinite(frequency) || frequency <= 0
            || !float.IsFinite(position_amount) || position_amount <= 0
            || !damping) throw new ArgumentException("unsupported Noise strength/frequency/positionAmount");
    }
}

sealed class NoiseField {
    readonly int[] permutation = new int[512];
    public NoiseField(uint seed) {
        var source = Enumerable.Range(0, 256).ToArray();
        for (int i = 255; i > 0; i--) {
            int other = (int)(ParticleLayer.Random(i, seed, 40) * (i + 1));
            (source[i], source[other]) = (source[other], source[i]);
        }
        for (int i = 0; i < 512; i++) permutation[i] = source[i & 255];
    }
    static double Fade(double value) => value * value * value * (value * (value * 6 - 15) + 10);
    static double Lerp(double a, double b, double weight) => a + (b - a) * weight;
    static double Gradient(int hash, double x, double y, double z) {
        int kind = hash & 15;
        double first = kind < 8 ? x : y;
        double second = kind < 4 ? y : kind == 12 || kind == 14 ? x : z;
        return ((kind & 1) == 0 ? first : -first) + ((kind & 2) == 0 ? second : -second);
    }
    double Perlin(double x, double y, double z) {
        int ix = (int)Math.Floor(x), iy = (int)Math.Floor(y), iz = (int)Math.Floor(z);
        int xi = ix & 255, yi = iy & 255, zi = iz & 255;
        x -= ix; y -= iy; z -= iz;
        double u = Fade(x), v = Fade(y), w = Fade(z);
        int a = permutation[xi] + yi, aa = permutation[a] + zi, ab = permutation[a + 1] + zi;
        int b = permutation[xi + 1] + yi, ba = permutation[b] + zi, bb = permutation[b + 1] + zi;
        return Lerp(
            Lerp(
                Lerp(Gradient(permutation[aa], x, y, z), Gradient(permutation[ba], x - 1, y, z), u),
                Lerp(Gradient(permutation[ab], x, y - 1, z), Gradient(permutation[bb], x - 1, y - 1, z), u), v),
            Lerp(
                Lerp(Gradient(permutation[aa + 1], x, y, z - 1), Gradient(permutation[ba + 1], x - 1, y, z - 1), u),
                Lerp(Gradient(permutation[ab + 1], x, y - 1, z - 1), Gradient(permutation[bb + 1], x - 1, y - 1, z - 1), u), v),
            w);
    }
    static readonly double[][] offsets = {
        new[]{0d,0d,0d}, new[]{31.416,47.853,12.793}, new[]{-23.457,-19.228,33.187},
    };
    (double x,double y,double z) Curl(double x, double y, double z) {
        const double epsilon = .001;
        double Potential(int index, double px, double py, double pz) => Perlin(px + offsets[index][0], py + offsets[index][1], pz + offsets[index][2]);
        double first = Potential(2, x, y + epsilon, z) - Potential(2, x, y - epsilon, z)
            - Potential(1, x, y, z + epsilon) + Potential(1, x, y, z - epsilon);
        double second = Potential(0, x, y, z + epsilon) - Potential(0, x, y, z - epsilon)
            - Potential(2, x + epsilon, y, z) + Potential(2, x - epsilon, y, z);
        double third = Potential(1, x + epsilon, y, z) - Potential(1, x - epsilon, y, z)
            - Potential(0, x, y + epsilon, z) + Potential(0, x, y - epsilon, z);
        return (first / (2 * epsilon), second / (2 * epsilon), third / (2 * epsilon));
    }
    public (float x,float y,float z) Velocity(float x, float y, float z, NoiseConfig config) {
        var velocity = Curl(x * config.frequency, y * config.frequency, z * config.frequency);
        double multiplier = config.strength * config.position_amount / (config.damping ? config.frequency : 1);
        return ((float)(velocity.x * multiplier), (float)(velocity.y * multiplier), (float)(velocity.z * multiplier));
    }
    public (float x,float y) Integrate(float originX, float originY, float age, NoiseConfig config, Func<float,(float x,float y)> baseAt) {
        config.Validate();
        if (age < 0 || !float.IsFinite(age)) throw new ArgumentException("invalid Noise age");
        const float step = 1f / 120f;
        int steps = (int)Math.Ceiling(age * 120 - .0000001);
        if (steps > 100000) throw new ArgumentException("Noise static-frame step limit exceeded");
        float x = 0, y = 0;
        for (int index = 0; index < steps; index++) {
            float elapsed = index * step, dt = Math.Min(step, age - elapsed), middle = elapsed + dt * .5f;
            var position = baseAt(middle);
            var velocity = Velocity(originX + position.x + x, originY + position.y + y, 0, config);
            x += velocity.x * dt; y += velocity.y * dt;
        }
        return (x, y);
    }
}
