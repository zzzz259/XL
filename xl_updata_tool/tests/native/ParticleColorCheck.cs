using System;
using System.IO;
using System.Threading;
using SFML.Graphics;

static class ParticleColorCheck {
    static string white = "", ramp = "";
    static void Check(bool value, string reason) { if (!value) throw new Exception(reason); }
    static void Near(float[] actual, float[] expected, string reason) {
        Check(actual.Length == expected.Length, reason + ": channel count");
        for (int i = 0; i < actual.Length; i++)
            Check(Math.Abs(actual[i] - expected[i]) < 0.00001f,
                $"{reason}: channel {i}, actual={actual[i]}, expected={expected[i]}");
    }
    static Part MakePart(string profile = "alpha_tint_v1") {
        return new Part {
            id = "color-check", kind = "particle", texture = white,
            particle = new ParticleConfig {
                version = 1,
                system = new ParticleSystemConfig {
                    duration = 1, simulation_speed = 1, max_particles = 4,
                    lifetime = new[]{1f,1f}, size = new[]{2f,2f},
                    rotation_radians = new[]{0f,0f}, color = new[]{1f,1f,1f,1f},
                    shape_size = new[]{0f,0f,0f}, shape_position = new[]{0f,0f,0f},
                    velocity_min = new[]{0f,0f,0f}, velocity_max = new[]{0f,0f,0f},
                    tiles = new[]{1,1}, cycles = 1, gravity = new[]{0f,0f,0f},
                    gravity_source = "unused",
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
        ramp = Path.Combine(args[0], "ramp.png");
        using (var image = new Image(1, 1, Color.White)) Check(image.SaveToFile(white), "white fixture save failed");
        using (var image = new Image(2, 1)) {
            image.SetPixel(0, 0, Color.Red); image.SetPixel(1, 0, Color.Blue);
            Check(image.SaveToFile(ramp), "ramp fixture save failed");
        }
        string uneven = Path.Combine(args[0], "uneven.png");
        using (var image = new Image(5, 5, Color.Transparent)) {
            image.SetPixel(2, 4, Color.Red);
            Check(image.SaveToFile(uneven), "uneven fixture save failed");
        }

        var gradient = MakePart();
        gradient.particle!.system.color = new[]{.8f,.6f,.4f,1f};
        gradient.particle.system.color_over_time = new ColorOverTime {
            mode = "gradient", color_times = new[]{0f,1f},
            colors = new[]{new[]{1f,0f,0f},new[]{0f,1f,0f}},
            alpha_times = new[]{0f,1f}, alpha = new[]{1f,0f},
        };
        Near(Pixel(gradient, 2, 2, time: 0), new[]{.8f,0f,0f,1f}, "gradient start");
        Near(Pixel(gradient, 2, 2), new[]{.2f,.15f,0f,.5f}, "gradient midpoint and initial color");
        Near(Pixel(gradient, 2, 2, time: 1), new[]{0f,0f,0f,0f}, "gradient end lifetime");

        var constant = MakePart();
        constant.particle!.system.color_over_time = new ColorOverTime {
            mode = "constant", color = new[]{.5f,.25f,1f,.75f},
        };
        Near(Pixel(constant, 2, 2), new[]{.375f,.1875f,.75f,.75f}, "constant color over time");

        var additive = MakePart("additive_alpha_v1");
        additive.particle!.system.color = new[]{.8f,.6f,.4f,.5f};
        Near(Pixel(additive, 2, 2, new Color(64,32,16,255)),
            new[]{64/255f+.4f,32/255f+.3f,16/255f+.2f,1f}, "source-alpha additive factor one");

        var legacy = MakePart();
        legacy.particle!.system.color = new[]{.8f,.6f,.4f,.5f};
        Near(Pixel(legacy, 2, 2), new[]{.4f,.3f,.2f,.5f}, "legacy alpha tint pixels");
        var legacyAdditive = MakePart("additive_v1");
        legacyAdditive.particle!.system.color = new[]{.8f,.6f,.4f,.5f};
        Near(Pixel(legacyAdditive, 2, 2), new[]{1.6f,1.2f,.8f,1f}, "legacy additive pixels");

        var rectangular = MakePart();
        rectangular.particle!.system.size = new[]{4f,4f};
        rectangular.particle.system.size_y = new[]{2f,2f};
        ParticleLayer.Validate(rectangular);
        var rectangleLayer = new ParticleLayer(rectangular);
        using (var canvas = new FloatCanvas(7,7)) {
            canvas.Clear(Color.Transparent);
            rectangleLayer.Draw(canvas, .5, 1, 0, 0, CancellationToken.None);
            Check(canvas.Pixels[(1 * canvas.Width + 3) * 4 + 3] == 0, "rectangular particle retained square height");
            Near(new[]{canvas.Pixels[(2 * canvas.Width + 3) * 4],canvas.Pixels[(2 * canvas.Width + 3) * 4 + 1],
                canvas.Pixels[(2 * canvas.Width + 3) * 4 + 2],canvas.Pixels[(2 * canvas.Width + 3) * 4 + 3]},
                new[]{1f,1f,1f,1f}, "rectangular particle visible row");
        }
        var tall = MakePart();
        tall.particle!.system.size = new[]{1f,1f};
        tall.particle.system.size_y = new[]{4f,4f};
        var tallLayer = new ParticleLayer(tall);
        float left = float.MaxValue, boundTop = float.MaxValue, right = float.MinValue, boundBottom = float.MinValue;
        tallLayer.IncludeBounds(ref left, ref boundTop, ref right, ref boundBottom);
        Check(boundTop <= -2 && boundBottom >= 2, "independent Y size is missing from bounds");

        var circle = MakePart();
        circle.particle!.system.size = new[]{1f,1f};
        circle.particle.system.shape_size = new[]{2f,1f,1f};
        circle.particle.system.circle_radius = 1;
        circle.particle.system.circle_arc_radians = 0.0000001f;
        ParticleLayer.Validate(circle);
        var circleLayer = new ParticleLayer(circle);
        using (var canvas = new FloatCanvas(9,9)) {
            canvas.Clear(Color.Transparent);
            circleLayer.Draw(canvas, .5, 1, 0, 0, CancellationToken.None);
            Near(new[]{canvas.Pixels[(4 * canvas.Width + 6) * 4],canvas.Pixels[(4 * canvas.Width + 6) * 4 + 1],
                canvas.Pixels[(4 * canvas.Width + 6) * 4 + 2],canvas.Pixels[(4 * canvas.Width + 6) * 4 + 3]},
                new[]{1f,1f,1f,1f}, "circle arc starts at positive X");
            Check(canvas.Pixels[(4 * canvas.Width + 2) * 4 + 3] == 0, "circle arc used the opposite side");
        }
        float circleLeft = float.MaxValue, circleTop = float.MaxValue, circleRight = float.MinValue, circleBottom = float.MinValue;
        circleLayer.IncludeBounds(ref circleLeft, ref circleTop, ref circleRight, ref circleBottom);
        Check(circleLeft <= -2 && circleRight >= 2 && circleTop <= -1 && circleBottom >= 1, "circle ellipse bounds");

        var unevenPart = MakePart();
        unevenPart.texture = uneven;
        unevenPart.particle!.system.tiles = new[]{1,7};
        Near(Pixel(unevenPart, 2, 2), new[]{1f,0f,0f,1f}, "fractional UV frame height");

        var rotated = MakePart();
        rotated.texture = ramp;
        rotated.particle!.system.size = new[]{4f,4f};
        rotated.particle.system.render_alignment = 2;
        rotated.particle.system.scaling_mode = 1;
        rotated.affine = new[]{0f,-1f,1f,0f,0f,0f};
        ParticleLayer.Validate(rotated);
        var layer = new ParticleLayer(rotated);
        using (var canvas = new FloatCanvas(7,7)) {
            canvas.Clear(Color.Transparent);
            layer.Draw(canvas, .5, 1, 0, 0, CancellationToken.None);
            int top = (2 * canvas.Width + 3) * 4, bottom = (4 * canvas.Width + 3) * 4;
            Near(new[]{canvas.Pixels[top],canvas.Pixels[top+1],canvas.Pixels[top+2],canvas.Pixels[top+3]},
                new[]{0f,0f,1f,1f}, "parent rotation top pixel");
            Near(new[]{canvas.Pixels[bottom],canvas.Pixels[bottom+1],canvas.Pixels[bottom+2],canvas.Pixels[bottom+3]},
                new[]{1f,0f,0f,1f}, "parent rotation bottom pixel");
        }

        var invalid = MakePart();
        invalid.particle!.system.color_over_time = new ColorOverTime {
            mode = "gradient", color_times = new[]{0f,1f},
            colors = new[]{new[]{1f,1f,1f},new[]{0f,0f,0f}},
            alpha_times = new[]{0f,.5f,1f}, alpha = new[]{1f,0f},
        };
        Reject(invalid, "gradient alpha times and values differ");
        invalid = MakePart();
        invalid.particle!.system.color_over_time = new ColorOverTime {mode = "fixed"};
        Reject(invalid, "unknown gradient mode");
        invalid = MakePart(); invalid.particle!.system.render_alignment = 1;
        Reject(invalid, "unevidenced render alignment");
        invalid = MakePart(); invalid.particle!.system.scaling_mode = 2;
        Reject(invalid, "unevidenced scaling mode");
        invalid = MakePart("additive_alpha_v1"); invalid.particle!.material.factor = 2;
        Reject(invalid, "wrong additive-alpha factor");

        Console.WriteLine("PASS particle gradients, exact additive, XY/circle shapes, fractional UV, affine and legacy pixels");
        return 0;
    }
}
