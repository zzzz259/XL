using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Threading;
using SFML.Graphics;

static class ParticleDynamicsCheck {
    static string white = "";
    static void Check(bool value, string reason) { if (!value) throw new Exception(reason); }
    static void Near(float actual, float expected, string reason, float tolerance = .0001f) {
        Check(Math.Abs(actual - expected) <= tolerance, $"{reason}: actual={actual}, expected={expected}");
    }
    static CurveData Linear() => new CurveData {keys = new[]{
        new CurveKey {time = 0, value = 0, inSlope = 0, outSlope = 1},
        new CurveKey {time = 1, value = 1, inSlope = 1, outSlope = 1},
    }};
    static Part MakePart(string profile = "alpha_tint_v1") {
        return new Part {
            id = "dynamics-check", kind = "particle", texture = white,
            particle = new ParticleConfig {
                version = 1,
                system = new ParticleSystemConfig {
                    duration = 1, simulation_speed = 1, max_particles = 8,
                    lifetime = new[]{1f,1f}, size = new[]{2f,2f}, rotation_radians = new[]{0f,0f},
                    color = new[]{1f,1f,1f,1f}, shape_size = new[]{0f,0f,0f}, shape_position = new[]{0f,0f,0f},
                    velocity_min = new[]{0f,0f,0f}, velocity_max = new[]{0f,0f,0f},
                    tiles = new[]{1,1}, cycles = 1, gravity = new[]{0f,0f,0f}, gravity_source = "unused",
                    bursts = new[]{new ParticleBurst {time = 0, count = 1, cycles = 1, probability = 1}},
                },
                material = new ParticleMaterial {
                    profile = profile, factor = profile == "additive_v1" ? 2 : 1,
                    tint = new[]{1f,1f,1f,1f}, dark_color = new[]{1f,1f,1f,1f},
                    main_power = 1, alpha = 1, alpha_ratio = 1,
                },
            },
        };
    }
    static MeshConfig Mesh() => new MeshConfig {
        kind = "mesh_quad_v1",
        positions = new[]{new[]{1f,-1f,0f},new[]{1f,1f,0f},new[]{-1f,1f,0f},new[]{-1f,-1f,0f}},
        uvs = new[]{new[]{0f,0f},new[]{0f,1f},new[]{1f,1f},new[]{1f,0f}},
        indices = new[]{0,1,2,0,2,3},
    };
    static float[] Pixel(Part part, int x, int y, Color? background = null, double time = .5) {
        ParticleLayer.Validate(part);
        var layer = new ParticleLayer(part);
        using var canvas = new FloatCanvas(5, 5);
        canvas.Clear(background ?? Color.Transparent);
        layer.Draw(canvas, time, 1, 0, 0, CancellationToken.None);
        int offset = (y * canvas.Width + x) * 4;
        return new[]{canvas.Pixels[offset],canvas.Pixels[offset+1],canvas.Pixels[offset+2],canvas.Pixels[offset+3]};
    }
    static void Reject(Part part, string reason) {
        try { ParticleLayer.Validate(part); throw new Exception("accepted " + reason); }
        catch (ArgumentException) { }
    }
    static int Main(string[] args) {
        Directory.CreateDirectory(args[0]);
        white = Path.Combine(args[0], "white.png");
        using (var image = new Image(1, 1, Color.White)) Check(image.SaveToFile(white), "fixture save failed");

        var linear = Linear();
        linear.Validate();
        Near(linear.Evaluate(.5f), .5f, "linear curve midpoint");
        Near(linear.Integrate(.5f), .125f, "linear curve integral");
        var hermite = new CurveData {keys = new[]{
            new CurveKey {time = 0, value = .6654663f, inSlope = 0, outSlope = .3345337f},
            new CurveKey {time = 1, value = 1, inSlope = .3345337f, outSlope = 1},
        }};
        Near(hermite.Evaluate(.5f), .83273315f, "Hermite midpoint");
        var extrema = hermite.Extrema();
        Near(extrema.min, .6654663f, "Hermite minimum");
        Near(extrema.max, 1f, "Hermite maximum");
        var scaled = new MinMaxCurve {mode = 1, scalar = 2, max = hermite};
        scaled.Validate();
        Near(scaled.Evaluate(.5f, 0, 0, 20), 1.6654663f, "curve scalar");
        var constants = new MinMaxCurve {mode = 3, scalar = 2, min_scalar = -1};
        constants.Validate();
        float constant = constants.Evaluate(.5f, 1, 7, 20);
        Check(constant >= -1 && constant <= 2, "constant random range");
        Near(constants.EvaluateIntegral(.5f, 1, 7, 20), constant * .5f, "constant integral");

        var two = new StartColor {mode = "two_colors", min = new[]{1f,0f,0f,1f}, max = new[]{0f,0f,1f,1f}};
        two.Validate();
        float random = ParticleLayer.Random(7, 7, 10);
        var color = two.Evaluate(7, 7);
        Near(color[0], 1 - random, "two-color interpolation");
        Near(color[2], random, "two-color interpolation blue");
        var fixedGradient = new StartColor {
            mode = "random_fixed_gradient", color_times = new[]{0f,.5f,1f},
            colors = new[]{new[]{1f,0f,0f},new[]{0f,1f,0f},new[]{0f,0f,1f}},
            alpha_times = new[]{0f,1f}, alpha = new[]{1f,1f},
        };
        fixedGradient.Validate();
        float fixedTime = ParticleLayer.Random(7, 7, 11);
        var fixedColor = fixedGradient.Evaluate(7, 7);
        int expected = fixedTime < .5f ? 0 : 1;
        Near(fixedColor[expected], 1, "fixed gradient right key");

        var over = new ColorOverTime {
            mode = "gradient", color_times = new[]{.4f,.6f}, colors = new[]{new[]{1f,0f,0f},new[]{0f,1f,0f}},
            alpha_times = new[]{.4f,.6f}, alpha = new[]{0f,1f},
        };
        var start = over.Multiply(new[]{1f,1f,1f,1f}, 0);
        Near(start[0], 1, "gradient first clamp"); Near(start[3], 0, "gradient first alpha clamp");
        var middle = over.Multiply(new[]{1f,1f,1f,1f}, .5f);
        Near(middle[0], .5f, "gradient midpoint red"); Near(middle[1], .5f, "gradient midpoint green"); Near(middle[3], .5f, "gradient midpoint alpha");
        var end = over.Multiply(new[]{1f,1f,1f,1f}, 1);
        Near(end[1], 1, "gradient last clamp"); Near(end[3], 1, "gradient last alpha clamp");

        var mesh = MakePart();
        mesh.particle!.system.render_mode = 4;
        mesh.particle.system.size = new[]{1f,1f};
        mesh.particle.mesh = Mesh();
        var meshPixel = Pixel(mesh, 1, 2);
        Near(meshPixel[3], 1, "mesh has actual two-by-two extent");
        var withoutMesh = MakePart();
        withoutMesh.particle!.system.render_mode = 4;
        Reject(withoutMesh, "mesh mode without exact mesh");
        var changedMesh = MakePart(); changedMesh.particle!.system.render_mode = 4;
        changedMesh.particle.mesh = Mesh(); changedMesh.particle.mesh.uvs[0][0] = 1;
        Reject(changedMesh, "Mesh UV differs from the evidenced geometry");
        var meshOnBillboard = MakePart(); meshOnBillboard.particle!.mesh = Mesh();
        Reject(meshOnBillboard, "Mesh config on billboard");

        var blend = MakePart("alpha_blend_v1");
        blend.particle!.system.color = new[]{1f,1f,1f,.5f};
        var blended = Pixel(blend, 2, 2, new Color(64,32,16,255));
        Near(blended[0], 64/255f*.5f+.5f, "alpha blend red");
        Near(blended[1], 32/255f*.5f+.5f, "alpha blend green");
        Near(blended[2], 16/255f*.5f+.5f, "alpha blend blue");
        Near(blended[3], 1, "alpha blend alpha");

        var legacy = MakePart();
        legacy.particle!.system.color = new[]{.8f,.6f,.4f,.5f};
        var old = Pixel(legacy, 2, 2);
        Near(old[0], .4f, "legacy red"); Near(old[1], .3f, "legacy green"); Near(old[2], .2f, "legacy blue"); Near(old[3], .5f, "legacy alpha");

        var uv = MakePart();
        uv.particle!.system.frame_mode = "random_constant";
        uv.particle.system.uv_row = "random";
        uv.particle.system.frame_range = new[]{0f,1f};
        uv.particle.system.tiles = new[]{1,7};
        ParticleLayer.Validate(uv);
        var first = Pixel(uv, 2, 2);
        var second = Pixel(uv, 2, 2);
        Check(first.SequenceEqual(second), "random UV is deterministic per particle");

        var multi = new CurveData {keys = new[]{
            new CurveKey {time = 0, value = 0, inSlope = 0, outSlope = 1},
            new CurveKey {time = .5f, value = .5f, inSlope = 1, outSlope = 1},
            new CurveKey {time = 1, value = 1, inSlope = 1, outSlope = 0},
        }};
        Near(multi.Evaluate(.5f), .5f, "multi-key midpoint");
        Near(multi.Integrate(.75f), .28125f, "multi-key integral");
        Near(multi.Integrate(-1), 0, "integral lower clamp");
        Near(multi.Integrate(2), .5f, "integral upper clamp");

        string ramp = Path.Combine(args[0], "ramp.png");
        using (var image = new Image(2, 1)) {
            image.SetPixel(0, 0, Color.Red); image.SetPixel(1, 0, Color.Blue);
            Check(image.SaveToFile(ramp), "ramp fixture save failed");
        }
        var meshUv = MakePart(); meshUv.texture = ramp;
        meshUv.particle!.system.render_mode = 4; meshUv.particle.system.size = new[]{4f,4f};
        meshUv.particle.mesh = Mesh();
        Near(Pixel(meshUv, 0, 2)[2], 1, "Mesh left edge uses the actual right-hand UV");
        var billboardUv = MakePart(); billboardUv.texture = ramp;
        billboardUv.particle!.system.size = new[]{4f,4f};
        Near(Pixel(billboardUv, 1, 2)[0], 1, "billboard UV must not change with Mesh support");

        var sized = MakePart(); sized.particle!.system.size = new[]{4f,4f};
        sized.particle.system.size_over_time = new MinMaxCurve {mode = 1, scalar = 2, max = Linear()};
        Near(Pixel(sized, 0, 2, time: .5)[3], 1, "Size multiplier reaches the actual edge");
        var rotated = MakePart(); rotated.texture = ramp; rotated.particle!.system.size = new[]{4f,4f};
        rotated.particle.system.rotation_over_time = new MinMaxCurve {mode = 3, scalar = MathF.PI / 2, min_scalar = MathF.PI / 2};
        Near(Pixel(rotated, 2, 0)[2], 1, "Rotation integrates angular velocity over age");
        Near(Pixel(rotated, 2, 0, time: 0)[0], .5f, "unrotated texture control");
        var moving = MakePart();
        moving.particle!.system.velocity_curves = new Dictionary<string,MinMaxCurve> {
            ["x"] = new MinMaxCurve {mode = 1, scalar = 4, max = Linear()},
        };
        Near(Pixel(moving, 3, 2)[3], 1, "Velocity integral displacement");
        Near(Pixel(moving, 4, 2)[3], 0, "Velocity does not use velocity times age");
        var returning = MakePart(); returning.particle!.system.size = new[]{2f,2f};
        returning.particle.system.velocity_curves = new Dictionary<string,MinMaxCurve> {
            ["x"] = new MinMaxCurve {mode = 1, scalar = 1, max = new CurveData {keys = new[]{
                new CurveKey {time = 0, value = 2, inSlope = -4, outSlope = -4},
                new CurveKey {time = 1, value = -2, inSlope = -4, outSlope = -4},
            }}},
        };
        var returningLayer = new ParticleLayer(returning);
        float left = float.MaxValue, top = float.MaxValue, right = float.MinValue, bottom = float.MinValue;
        returningLayer.IncludeBounds(ref left, ref top, ref right, ref bottom);
        Check(right >= .5f + MathF.Sqrt(2), "bounds include a return trajectory's intermediate displacement");

        var bursts = MakePart(); bursts.particle!.system.max_particles = 4;
        bursts.particle.system.color = new[]{1f,1f,1f,.25f};
        bursts.particle.system.bursts = new[]{
            new ParticleBurst {time = 0, count = 8, cycles = 1, probability = 1},
            new ParticleBurst {time = 0, count = 3, cycles = 1, probability = 1},
        };
        Near(Pixel(bursts, 2, 2, time: 0)[3], 1 - MathF.Pow(.75f, 4), "multiple bursts retain IDs and particle capacity");

        var orbital = MakePart();
        orbital.particle!.system.shape_size = new[]{1f,1f,1f};
        orbital.particle.system.circle_radius = 1; orbital.particle.system.circle_arc_radians = .0000001f;
        orbital.particle.system.orbital_z = new MinMaxCurve {mode = 3, scalar = MathF.PI / 2, min_scalar = MathF.PI / 2};
        Near(Pixel(orbital, 3, 1)[3], 1, "Z orbital angular velocity rotates the XY position");
        var withoutOrbit = MakePart(); withoutOrbit.particle!.system.shape_size = new[]{1f,1f,1f};
        withoutOrbit.particle.system.circle_radius = 1; withoutOrbit.particle.system.circle_arc_radians = .0000001f;
        Near(Pixel(withoutOrbit, 3, 3)[3], 0, "unrotated orbital control");

        float inner = .4f, mean = 0;
        for (int id = 1; id <= 1000; id++) {
            float radius = ParticleLayer.CircleRadius(id, 7, 1, .6f);
            Check(radius >= inner && radius <= 1, "Circle annulus bounds");
            mean += radius / 1000;
        }
        Near(mean, 2f / 3f * (1 - inner * inner * inner) / (1 - inner * inner), "Circle area-uniform mean", .01f);

        var noise = new NoiseConfig {strength = .19f, frequency = 1.26f, damping = true, position_amount = 1};
        noise.Validate();
        var field = new NoiseField(7);
        var displacement = field.Integrate(0, 0, 1, noise, _ => (0f, 0f));
        var repeated = field.Integrate(0, 0, 1, noise, _ => (0f, 0f));
        Near(displacement.x, repeated.x, "Noise deterministic X");
        Near(displacement.y, repeated.y, "Noise deterministic Y");
        Check(Math.Abs(displacement.x) + Math.Abs(displacement.y) > .000001f, "Noise moves a particle");
        var doubled = new NoiseConfig {strength = noise.strength * 2, frequency = noise.frequency, damping = true, position_amount = 1};
        var velocity = field.Velocity(.1f, .2f, .3f, noise);
        var doubleVelocity = field.Velocity(.1f, .2f, .3f, doubled);
        Near(doubleVelocity.x, velocity.x * 2, "Noise strength scales instantaneous X velocity");
        Near(doubleVelocity.y, velocity.y * 2, "Noise strength scales instantaneous Y velocity");
        var nearby = field.Integrate(.1f, 0, 1, noise, _ => (0f, 0f));
        Check(Math.Abs(nearby.x - displacement.x) + Math.Abs(nearby.y - displacement.y) > .000001f, "Noise depends on position");
        var zero = field.Integrate(0, 0, 0, noise, _ => (0f, 0f));
        Near(zero.x, 0, "Noise zero-age X"); Near(zero.y, 0, "Noise zero-age Y");
        var unit = new NoiseConfig {strength = 1, frequency = 1, damping = true, position_amount = 1};
        float epsilon = .001f;
        var divergence = (field.Velocity(.1f + epsilon, .2f, .3f, unit).x - field.Velocity(.1f - epsilon, .2f, .3f, unit).x
            + field.Velocity(.1f, .2f + epsilon, .3f, unit).y - field.Velocity(.1f, .2f - epsilon, .3f, unit).y
            + field.Velocity(.1f, .2f, .3f + epsilon, unit).z - field.Velocity(.1f, .2f, .3f - epsilon, unit).z) / (2 * epsilon);
        Near(divergence, 0, "Curl field is divergence-free", .01f);
        var noisy = MakePart(); noisy.particle!.system.noise = noise;
        noisy.particle.system.loop = true; noisy.particle.system.prewarm = true;
        var noisyLayer = new ParticleLayer(noisy);
        left = float.MaxValue; top = float.MaxValue; right = float.MinValue; bottom = float.MinValue;
        noisyLayer.IncludeBounds(ref left, ref top, ref right, ref bottom, 0);
        Check(left < right && top < bottom && right - left < 100 && bottom - top < 100, "Noise uses static-frame bounds");

        Console.WriteLine("PASS particle curves, random colors, Mesh UV, alpha blend, dynamics, Circle, Noise and legacy pixels");
        return 0;
    }
}
