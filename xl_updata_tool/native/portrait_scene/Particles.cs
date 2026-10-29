// CPU float material path. RGBA8 is used only for source texture/capture and final presentation.
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json.Serialization;
using System.Threading;
using SFML.Graphics;

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class ParticleConfig {
    public int version { get; set; }
    public ParticleSystemConfig system { get; set; } = new();
    public ParticleMaterial material { get; set; } = new();
}
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class ParticleBurst {
    public float time { get; set; }
    public float count { get; set; }
    public int cycles { get; set; }
    public float interval { get; set; }
    public float probability { get; set; }
}
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class ParticleSystemConfig {
    public float duration { get; set; }
    public bool loop { get; set; }
    public bool prewarm { get; set; }
    public float simulation_speed { get; set; }
    public float rate { get; set; }
    public int max_particles { get; set; }
    public float[] lifetime { get; set; } = Array.Empty<float>();
    public float[] size { get; set; } = Array.Empty<float>();
    public float[] rotation_radians { get; set; } = Array.Empty<float>();
    public float[] color { get; set; } = Array.Empty<float>();
    public float[] shape_size { get; set; } = Array.Empty<float>();
    public float[] shape_position { get; set; } = Array.Empty<float>();
    public float[] velocity_min { get; set; } = Array.Empty<float>();
    public float[] velocity_max { get; set; } = Array.Empty<float>();
    public int[] tiles { get; set; } = Array.Empty<int>();
    public float uv_frame_end { get; set; }
    public float cycles { get; set; }
    public uint seed { get; set; }
    public float gravity_modifier { get; set; }
    public float[] gravity { get; set; } = new float[]{0,0,0};
    public string gravity_source { get; set; } = "";
    public ParticleBurst[] bursts { get; set; } = Array.Empty<ParticleBurst>();
}
[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
sealed class ParticleMaterial {
    public string profile { get; set; } = "";
    public float[] tint { get; set; } = Array.Empty<float>();
    public float[] dark_color { get; set; } = Array.Empty<float>();
    public float factor { get; set; }
    public float dead_strength { get; set; }
    public float main_power { get; set; }
    public float alpha { get; set; }
    public float alpha_ratio { get; set; }
    public float blend_amount { get; set; }
}
sealed partial class FloatCanvas {
    public readonly int Width, Height;
    public readonly float[] Pixels;
    public float Peak;
    public FloatCanvas(int width, int height) { Width = width; Height = height; Pixels = new float[checked(width * height * 4)]; }
    public void Clear(Color color) {
        float a = color.A / 255f;
        for (int i = 0; i < Pixels.Length; i += 4) { Pixels[i] = color.R / 255f * a; Pixels[i+1] = color.G / 255f * a; Pixels[i+2] = color.B / 255f * a; Pixels[i+3] = a; }
    }
    // RGB storage is premultiplied, with an independent coverage alpha for portable PNG output.
    // Keep HDR until Bytes(): later normal layers must be able to attenuate over-white values.
    public void Blend(int i, float r, float g, float b, float a, bool additive) {
        float dst = additive ? 1 : 1 - Math.Clamp(a, 0, 1);
        Pixels[i] = r * a + Pixels[i] * dst;
        Pixels[i+1] = g * a + Pixels[i+1] * dst;
        Pixels[i+2] = b * a + Pixels[i+2] * dst;
        Pixels[i+3] = Math.Clamp(a + Pixels[i+3] * (1 - Math.Clamp(a, 0, 1)), 0, 1);
        Peak = Math.Max(Peak, Math.Max(Pixels[i], Math.Max(Pixels[i+1], Pixels[i+2])));
    }
    public void Capture(byte[] rgba) {
        // Captured RGB is already premultiplied: avoid divide then multiply per pixel.
        for (int i = 0; i < Pixels.Length; i += 4) {
            float a = rgba[i+3] / 255f;
            // PMA normal slots may carry emissive RGB even when alpha is zero.
            if ((rgba[i] | rgba[i+1] | rgba[i+2] | rgba[i+3]) == 0) continue;
            float inverse=1-a;
            Pixels[i]=rgba[i]/255f+Pixels[i]*inverse;
            Pixels[i+1]=rgba[i+1]/255f+Pixels[i+1]*inverse;
            Pixels[i+2]=rgba[i+2]/255f+Pixels[i+2]*inverse;
            Pixels[i+3]=a+Pixels[i+3]*inverse;
            Peak=Math.Max(Peak,Math.Max(Pixels[i],Math.Max(Pixels[i+1],Pixels[i+2])));
        }
    }
    public byte[] Bytes(bool pma) {
        var bytes = new byte[Pixels.Length];
        for (int i = 0; i < Pixels.Length; i += 4) {
            float a = Pixels[i+3];
            for (int c = 0; c < 3; c++) bytes[i+c] = (byte)Math.Clamp((int)MathF.Round(Pixels[i+c] * (pma || a <= 0 ? 1 : 1 / a) * 255), 0, 255);
            bytes[i+3] = (byte)Math.Clamp((int)MathF.Round(a * 255), 0, 255);
        }
        return bytes;
    }
}
sealed class ParticleLayer {
    public readonly Part Spec;
    readonly ParticleSystemConfig s;
    readonly ParticleMaterial m;
    readonly byte[] texture;
    readonly int tw, th;
    public float Natural => s.simulation_speed == 0 ? 0 : s.duration / s.simulation_speed;
    public ParticleLayer(Part spec) {
        Spec = spec; s = spec.particle!.system; m = spec.particle.material;
        using var image = new Image(spec.texture);
        tw = (int)image.Size.X; th = (int)image.Size.Y; texture = image.Pixels;
        if (tw % s.tiles[0] != 0 || th % s.tiles[1] != 0) throw new ArgumentException("Particle tiles do not divide texture");
    }
    public static void Validate(Part p) {
        void Check(bool b, string reason) { if (!b) throw new ArgumentException($"Part {p.id}: unsupported particle config: {reason}"); }
        void Array(float[] v, int n) => Check(v.Length == n && v.All(float.IsFinite), "invalid vector/range");
        Check(p.particle != null && p.particle.version == 1 && File.Exists(p.texture), "version or texture");
        var s = p.particle!.system; var m = p.particle.material;
        foreach (var v in new[] {s.duration,s.simulation_speed,s.rate,s.uv_frame_end,s.cycles,m.factor,m.dead_strength,m.main_power,m.alpha,m.alpha_ratio,m.blend_amount}) Check(float.IsFinite(v), "nonfinite scalar");
        foreach (var v in new[] {s.lifetime,s.size,s.rotation_radians}) { Array(v,2); Check(v[1] >= v[0], "reversed range"); }
        Array(s.gravity,3); Check(float.IsFinite(s.gravity_modifier), "gravity_modifier");
        Check(s.gravity_modifier == 0 || !string.IsNullOrEmpty(s.gravity_source), "gravity provenance missing");
        foreach(var burst in s.bursts) Check(burst.time == 0 && float.IsFinite(burst.count) && burst.count == MathF.Floor(burst.count) && burst.count >= 0 && burst.count <= 100000 && burst.cycles == 1 && burst.interval >= 0 && float.IsFinite(burst.interval) && burst.probability == 1, "burst variant");
        Array(s.color,4); Array(m.tint,4); Array(m.dark_color,4);
        foreach (var v in new[] {s.shape_size,s.shape_position,s.velocity_min,s.velocity_max}) Array(v,3);
        Check(s.duration > 0 && s.duration <= 3600 && s.simulation_speed >= 0 && s.rate >= 0 && s.rate <= 100000 && s.max_particles > 0 && s.max_particles <= 100000, "emitter limits");
        Check(s.lifetime[0] > 0 && s.lifetime[1] <= 3600 && s.size[0] > 0, "lifetime/size");
        Check(Enumerable.Range(0,3).All(i => s.velocity_max[i] >= s.velocity_min[i]), "shape/velocity");
        Check(s.color.All(v => v >= 0 && v <= 1) && m.tint.All(v => v >= 0) && m.dark_color.All(v => v >= 0), "color");
        Check(s.tiles.Length == 2 && s.tiles.All(v => v > 0 && v <= 256), "tiles");
        Check(s.cycles > 0 && s.uv_frame_end >= 0 && s.uv_frame_end <= 1, "UV curve");
        Check(m.profile is "additive_v1" or "alpha_tint_v1", "unknown material profile");
        Check(m.factor == (m.profile == "additive_v1" ? 2 : 1) && m.blend_amount == 0 && m.main_power >= 0 && m.alpha >= 0 && m.alpha_ratio >= 0 && m.dead_strength >= 0, "material variant");
    }
    static float Random(long id, uint seed, uint lane) {
        ulong x = unchecked((ulong)id * 0x9e3779b97f4a7c15UL + seed + lane * 0x85ebca6bUL);
        x ^= x >> 30; x *= 0xbf58476d1ce4e5b9UL; x ^= x >> 27; x *= 0x94d049bb133111ebUL; x ^= x >> 31;
        return (x >> 40) / 16777216f;
    }
    float R(long id, uint lane, float lo, float hi) => lo + (hi-lo) * Random(id,s.seed,lane);
    public void IncludeBounds(ref float left, ref float top, ref float right, ref float bottom) {
        // Conservative full-lifetime envelope, including prewarm, motion, rotation, parent shear/scale.
        float radius = s.size[1] * .7071068f;
        float[] lo = new float[2], hi = new float[2];
        for (int axis = 0; axis < 2; axis++) {
            float gravityTravel=.5f*s.gravity[axis]*s.gravity_modifier*s.lifetime[1]*s.lifetime[1];
            lo[axis] = s.shape_position[axis] - Math.Abs(s.shape_size[axis])/2 + Math.Min(0,s.velocity_min[axis]*s.lifetime[1]) - radius + Math.Min(0,gravityTravel);
            hi[axis] = s.shape_position[axis] + Math.Abs(s.shape_size[axis])/2 + Math.Max(0,s.velocity_max[axis]*s.lifetime[1]) + radius + Math.Max(0,gravityTravel);
        }
        var a = Spec.affine;
        foreach (float x in new[] {lo[0],hi[0]}) foreach (float y in new[] {lo[1],hi[1]}) {
            float px=a[0]*x+a[1]*y+a[4], py=-(a[2]*x+a[3]*y+a[5]);
            left=Math.Min(left,px); right=Math.Max(right,px); top=Math.Min(top,py); bottom=Math.Max(bottom,py);
        }
    }
    public void Draw(FloatCanvas canvas, double time, float fit, float centerX, float centerY, CancellationToken token) {
        double t=time*s.simulation_speed + (s.prewarm && s.loop ? s.duration : 0);
        double end=s.loop ? t : Math.Min(t,s.duration);
        var births=new List<(double time,long id)>();
        if(!double.IsFinite(t)||end*s.rate>1000000||t/s.duration>1000000)throw new ArgumentException("Particle simulation time exceeds bounded window");
        long last=s.rate>0?(long)Math.Floor(end*s.rate+1e-7):-1;
        if(last>1000000)throw new ArgumentException("Particle simulation exceeds bounded 1M birth window");
        for(long id=1;id<=last;id++) {
            if((id&4095)==0)token.ThrowIfCancellationRequested();
            double birth=id/s.rate;
            if(!s.loop && birth>=s.duration)continue;
            births.Add((birth,id));
        }
        long serial=0;
        int repeats=s.loop?(int)Math.Floor(t/s.duration):0;
        if(repeats>1000000)throw new ArgumentException("Particle burst cycle limit exceeded");
        for(int cycle=0;cycle<=repeats;cycle++)foreach(var burst in s.bursts) {
            double birth=cycle*s.duration+burst.time;
            for(int j=0;j<burst.count;j++) {
                if(birth<=t)births.Add((birth,-1-serial)); serial++;
                if(births.Count>1000000)throw new ArgumentException("Particle simulation exceeds bounded 1M births");
            }
        }
        births.Sort((a,b)=>{int value=a.time.CompareTo(b.time);return value!=0?value:a.id.CompareTo(b.id);});
        var deaths=new PriorityQueue<double,double>();
        var live=new List<(long id,double age,float life)>();
        foreach(var birth in births) {
            token.ThrowIfCancellationRequested();
            while(deaths.TryPeek(out double death,out _)&&death<=birth.time+1e-7)deaths.Dequeue();
            if(deaths.Count>=s.max_particles)continue;
            float life=R(birth.id,1,s.lifetime[0],s.lifetime[1]);
            double dies=birth.time+life;deaths.Enqueue(dies,dies);
            double age=t-birth.time;if(age>=0&&age<life)live.Add((birth.id,age,life));
        }
        foreach(var item in live) {
            long id=item.id;float age=(float)item.age,life=item.life;
            float x=R(id,2,-Math.Abs(s.shape_size[0])/2,Math.Abs(s.shape_size[0])/2)+s.shape_position[0]+R(id,3,s.velocity_min[0],s.velocity_max[0])*age+.5f*s.gravity[0]*s.gravity_modifier*age*age;
            float y=R(id,4,-Math.Abs(s.shape_size[1])/2,Math.Abs(s.shape_size[1])/2)+s.shape_position[1]+R(id,5,s.velocity_min[1],s.velocity_max[1])*age+.5f*s.gravity[1]*s.gravity_modifier*age*age;
            float size=R(id,6,s.size[0],s.size[1]), angle=R(id,7,s.rotation_radians[0],s.rotation_radians[1]);
            int frame=Math.Min(s.tiles[0]*s.tiles[1]-1,(int)(Math.Min(1,(age/life*s.cycles)%1*s.uv_frame_end)*s.tiles[0]*s.tiles[1]));
            Stamp(canvas,x,y,size,angle,frame,fit,centerX,centerY,token);
        }
    }
    void Stamp(FloatCanvas canvas,float x,float y,float size,float angle,int frame,float fit,float cx,float cy,CancellationToken token) {
        var a=Spec.affine; float co=MathF.Cos(angle),si=MathF.Sin(angle);
        float aa=(a[0]*co+a[1]*si)*size*fit, bb=(-a[0]*si+a[1]*co)*size*fit;
        float cc=-(a[2]*co+a[3]*si)*size*fit, dd=-(-a[2]*si+a[3]*co)*size*fit;
        float px=(a[0]*x+a[1]*y+a[4]-cx)*fit+canvas.Width/2f, py=(-(a[2]*x+a[3]*y+a[5])-cy)*fit+canvas.Height/2f;
        float det=aa*dd-bb*cc;
        float rx=(Math.Abs(aa)+Math.Abs(bb))/2,ry=(Math.Abs(cc)+Math.Abs(dd))/2;
        int x0=Math.Max(0,(int)MathF.Floor(px-rx)),x1=Math.Min(canvas.Width,(int)MathF.Ceiling(px+rx));
        int y0=Math.Max(0,(int)MathF.Floor(py-ry)),y1=Math.Min(canvas.Height,(int)MathF.Ceiling(py+ry));
        int cw=tw/s.tiles[0],ch=th/s.tiles[1],col=frame%s.tiles[0],row=s.tiles[1]-1-frame/s.tiles[0];
        for (int iy=y0; iy<y1; iy++) {
            token.ThrowIfCancellationRequested();
            for (int ix=x0; ix<x1; ix++) {
                float dx=ix+.5f-px,dy=iy+.5f-py;
                float u=(dd*dx-bb*dy)/det+.5f,v=.5f-(aa*dy-cc*dx)/det;
                if (u<0 || u>=1 || v<0 || v>=1) continue;
                float sx=u*cw-.5f,sy=v*ch-.5f; int tx=(int)MathF.Floor(sx),ty=(int)MathF.Floor(sy);
                float fx=sx-tx,fy=sy-ty;
                float Sample(int channel) {
                    float P(int xx,int yy) => texture[((row*ch+Math.Clamp(yy,0,ch-1))*tw+col*cw+Math.Clamp(xx,0,cw-1))*4+channel]/255f;
                    return (P(tx,ty)*(1-fx)+P(tx+1,ty)*fx)*(1-fy)+(P(tx,ty+1)*(1-fx)+P(tx+1,ty+1)*fx)*fy;
                }
                float r=Sample(0)*s.color[0]*Spec.color[0]*m.tint[0],g=Sample(1)*s.color[1]*Spec.color[1]*m.tint[1],b=Sample(2)*s.color[2]*Spec.color[2]*m.tint[2];
                float alpha=Sample(3)*s.color[3]*Spec.color[3]*m.tint[3]; bool additive=m.profile=="additive_v1";
                if (additive) { if (r+g+b<m.dead_strength) alpha=0; alpha*=m.factor; }
                else alpha=Math.Clamp(alpha*m.alpha,0,1)*m.alpha_ratio;
                float factor=m.factor*(additive?1:m.main_power);
                canvas.Blend((iy*canvas.Width+ix)*4,r*factor*m.dark_color[0],g*factor*m.dark_color[1],b*factor*m.dark_color[2],alpha*m.dark_color[3],additive);
            }
        }
    }
}
