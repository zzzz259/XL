// Persistent Spine frame capture -> float materials -> native encode or one owned FFmpeg pipe.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using SFML.Graphics;
using SFML.System;
using SkiaSharp;

static class FramePipeline {
    public const long MaxCanvasPixels=64L*1024*1024;
    public static long ValidateCanvasDimensions(int width,int height,long? memoryBudget=null) {
        long pixels=(long)width*height;
        if(width<=0||height<=0||pixels>MaxCanvasPixels)throw new ArgumentException($"Canvas {width}x{height} exceeds supported {MaxCanvasPixels} pixel limit");
        // Float RGBA + source/readback/output/Skia buffers and native target allowance.
        long estimated=checked(pixels*40);
        long available=memoryBudget??Math.Max(0,GC.GetGCMemoryInfo().TotalAvailableMemoryBytes-GC.GetTotalMemory(false));
        if(estimated>available)throw new ArgumentException($"Canvas {width}x{height} requires approximately {estimated} bytes; available process memory budget is {available} bytes");
        return estimated;
    }
    static void Log(object value) => Console.WriteLine(JsonSerializer.Serialize(value));
    static void Advance(List<ScenePart> parts,float dt,float fps,CancellationToken token) {
        for (double t=0;t<dt;) { token.ThrowIfCancellationRequested(); float step=(float)Math.Min(Math.Min(1f/60,1f/fps),dt-t); foreach(var p in parts)p.Advance(step); t+=step; }
        foreach(var p in parts)p.Advance(0);
    }
    static Color Background(Settings s) {
        if(s.transparent)return Color.Transparent;
        string v=s.background_color.TrimStart('#');
        if(v.Length is not (6 or 8))throw new ArgumentException("Invalid background_color");
        return new Color(Convert.ToByte(v[..2],16),Convert.ToByte(v.Substring(2,2),16),Convert.ToByte(v.Substring(4,2),16),v.Length==8?Convert.ToByte(v.Substring(6,2),16):(byte)255);
    }
    static Process Encoder(string filename,int width,int height,Settings s) {
        string ffmpeg=Path.Combine(AppContext.BaseDirectory,"ffmpeg.exe");
        if(!File.Exists(ffmpeg))throw new FileNotFoundException("Bundled encoder missing",ffmpeg);
        var start=new ProcessStartInfo(ffmpeg){UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardError=true};
        void Add(params string[] args) {foreach(var a in args)start.ArgumentList.Add(a);}
        Add("-v","error","-y","-f","rawvideo","-pixel_format","rgba","-video_size",$"{width}x{height}","-framerate",s.fps.ToString(CultureInfo.InvariantCulture),"-i","pipe:0","-an");
        switch(s.format.ToLowerInvariant()) {
            case "mp4": Add("-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuv420p","-movflags","+faststart");break;
            case "webm": Add("-c:v","libvpx-vp9","-pix_fmt","yuva420p");break;
            case "gif": Add("-loop",s.loop?"0":"-1");break;
            default:throw new ArgumentException($"Unsupported streamed format {s.format}");
        }
        Add(filename); return Process.Start(start)??throw new IOException("Encoder did not start");
    }
    static void Kill(Process process) { try {if(!process.HasExited)process.Kill(true);}catch(InvalidOperationException){} }
    static void ValidateVideo(string path,CancellationToken token) {
        var start=new ProcessStartInfo(Path.Combine(AppContext.BaseDirectory,"ffmpeg.exe")){UseShellExecute=false,CreateNoWindow=true,RedirectStandardError=true};
        foreach(var arg in new[]{"-v","error","-xerror","-i",path,"-f","null","-"})start.ArgumentList.Add(arg);
        using var process=Process.Start(start)??throw new IOException("Video verification did not start");
        using var registration=token.Register(()=>Kill(process));
        var errors=process.StandardError.ReadToEndAsync(); process.WaitForExitAsync(token).GetAwaiter().GetResult();
        token.ThrowIfCancellationRequested(); if(process.ExitCode!=0)throw new IOException("Video verification failed: "+errors.GetAwaiter().GetResult());
    }
    public static int Run(Scene scene,CancellationToken token) {
        var s=scene.settings; var parts=new List<ScenePart>(); var particles=new Dictionary<Part,ParticleLayer>();
        string? staged=null; Process? encoder=null;
        try {
            Log(new {phase="load",parts=scene.parts.Length,material_buffer="cpu_float32",output_mapping="clip_at_presentation",alpha_policy="premultiplied_accumulation_coverage_alpha",color_space="encoded_rgb_game_space_unverified"});
            foreach(var spec in scene.parts) {token.ThrowIfCancellationRequested();if(spec.kind=="particle")particles[spec]=new ParticleLayer(spec);else parts.Add(new ScenePart(spec,s));}
            float natural=Math.Max(parts.Count>0?parts.Max(p=>p.TimelineDuration()):0,particles.Count>0?particles.Values.Max(p=>p.Natural):0);
            float snapshot=scene.snapshot_policy=="entrance_complete"?Math.Max(s.time_offset,parts.Count>0?parts.Max(p=>p.TimelineDuration(true)):0):s.time_offset;
            bool still=s.format.Equals("Png",StringComparison.OrdinalIgnoreCase)||s.format.Equals("Jpg",StringComparison.OrdinalIgnoreCase);
            float duration=s.duration.HasValue&&s.duration>=0?s.duration.Value:Math.Max(natural/s.speed,1/s.fps);
            float warm=s.warm_up<0?natural*-s.warm_up:s.warm_up;
            double initial=warm+(still?snapshot:s.time_offset);
            Log(new {phase="bounds",duration,snapshot_time=snapshot});
            Advance(parts,(float)initial,s.fps,token);
            float left=float.PositiveInfinity,top=float.PositiveInfinity,right=float.NegativeInfinity,bottom=float.NegativeInfinity;
            void Bounds(){foreach(var p in parts)p.IncludeBounds(ref left,ref top,ref right,ref bottom);}
            Bounds(); foreach(var p in particles.Values)p.IncludeBounds(ref left,ref top,ref right,ref bottom);
            if(!still)for(double t=0;t<duration*s.speed;) {float dt=(float)Math.Min(Math.Min(1f/60,s.speed/s.fps),duration*s.speed-t);Advance(parts,dt,s.fps,token);t+=dt;Bounds();}
            if(!float.IsFinite(left)||right<=left||bottom<=top)throw new ArgumentException("Empty scene bounds");
            float fit=Math.Min(s.scale,(s.max_resolution-s.margin*2)/Math.Max(right-left,bottom-top));
            // Preserve the established float fitting/rounding, but validate the
            // complete dimensions (including margin and encoder padding) before conversion.
            double canvasWidth=Math.Max(1,Math.Ceiling((right-left)*fit)+2d*s.margin);
            double canvasHeight=Math.Max(1,Math.Ceiling((bottom-top)*fit)+2d*s.margin);
            if(!still&&s.format.Equals("Mp4",StringComparison.OrdinalIgnoreCase)){canvasWidth+=canvasWidth%2;canvasHeight+=canvasHeight%2;}
            if(!double.IsFinite(canvasWidth)||!double.IsFinite(canvasHeight)||canvasWidth>int.MaxValue||canvasHeight>int.MaxValue)
                throw new ArgumentException("Canvas dimensions are nonfinite or exceed integer range");
            int w=checked((int)canvasWidth),h=checked((int)canvasHeight);
            ValidateCanvasDimensions(w,h);
            float cx=(left+right)/2,cy=(top+bottom)/2;
            foreach(var p in parts)p.Reset(s);Advance(parts,(float)initial,s.fps,token);
            string output=Path.GetFullPath(scene.output); Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            staged=Path.Combine(Path.GetDirectoryName(output)!,"."+Path.GetFileNameWithoutExtension(output)+"."+Guid.NewGuid().ToString("N")+".partial"+Path.GetExtension(output));
            using var target=new RenderTexture((uint)w,(uint)h);
            using var view=new View(new Vector2f(cx,cy),new Vector2f(w/fit,h/fit));target.SetView(view);
            var canvas=new FloatCanvas(w,h);
            var layers=scene.parts.Select((spec,index)=>(spec,index)).OrderBy(x=>x.spec.Order[0]).ThenBy(x=>x.spec.Order[1]).ThenBy(x=>x.spec.Order[2]).ThenBy(x=>x.spec.Order[3]).ThenByDescending(x=>x.index).ToArray();
            var bySpec=parts.ToDictionary(p=>p.Spec);
            int frames=still?1:Math.Max(1,(int)Math.Ceiling(duration*s.fps));
            Log(new {phase="export_start",width=w,height=h,duration,frames});
            Task<string>? encoderErrors=null;
            if(!still){encoder=Encoder(staged,w,h,s);encoderErrors=encoder.StandardError.ReadToEndAsync();}
            using var cancelEncoder=token.Register(()=>{if(encoder!=null)Kill(encoder);});
            var layerMilliseconds=new Dictionary<string,double>();
            var renderWatch=Stopwatch.StartNew();
            for(int frame=0;frame<frames;frame++) {
                token.ThrowIfCancellationRequested();
                canvas.Clear(Background(s));
                foreach(var item in layers) {
                    token.ThrowIfCancellationRequested();
                    long layerStarted=Stopwatch.GetTimestamp();
                    if(item.spec.kind=="particle")particles[item.spec].Draw(canvas,initial+frame/s.fps*s.speed,fit,cx,cy,token);
                    else {
                        bySpec[item.spec].DrawFloat(target,canvas,token);
                    }
                    layerMilliseconds[item.spec.id]=layerMilliseconds.GetValueOrDefault(item.spec.id)+Stopwatch.GetElapsedTime(layerStarted).TotalMilliseconds;
                }
                byte[] bytes=canvas.Bytes(still ? s.pma : s.format.Equals("Mp4",StringComparison.OrdinalIgnoreCase));
                if(still) {
                    using var bitmap=new SKBitmap(new SKImageInfo(w,h,SKColorType.Rgba8888,s.pma?SKAlphaType.Premul:SKAlphaType.Unpremul));
                    Marshal.Copy(bytes,0,bitmap.GetPixels(),bytes.Length);
                    using var image=SKImage.FromBitmap(bitmap);using var data=image.Encode(s.format.Equals("Jpg",StringComparison.OrdinalIgnoreCase)?SKEncodedImageFormat.Jpeg:SKEncodedImageFormat.Png,100);
                    using var stream=File.Create(staged);data.SaveTo(stream);
                } else encoder!.StandardInput.BaseStream.WriteAsync(bytes.AsMemory(),token).AsTask().GetAwaiter().GetResult();
                if(frame+1==frames||frame%(Math.Max(1,(int)s.fps))==0) {Log(new {phase="export",total=frames,current=frame+1});Log(new {phase="render_profile",current=frame+1,elapsed_ms=renderWatch.Elapsed.TotalMilliseconds,layer_ms=layerMilliseconds});}
                if(frame+1<frames)Advance(parts,s.speed/s.fps,s.fps,token);
            }
            if(encoder!=null) {
                Log(new {phase="encode"});encoder.StandardInput.Close();encoder.WaitForExitAsync(token).GetAwaiter().GetResult();
                token.ThrowIfCancellationRequested();if(encoder.ExitCode!=0)throw new IOException("Encoder failed: "+encoderErrors!.GetAwaiter().GetResult());
                Log(new {phase="validate"});ValidateVideo(staged,token);
            }
            token.ThrowIfCancellationRequested();if(!File.Exists(staged)||new FileInfo(staged).Length==0)throw new IOException("No rendered output");
            File.Move(staged,output,true);staged=null;
            Log(new {phase="complete",output,width=w,height=h,duration=still?0:duration,snapshot_time=snapshot,bounds=new[]{left,top,right,bottom},float_peak=canvas.Peak,frames,parts=parts.Select(p=>new {id=p.Spec.id,animation=p.AnimationState.GetCurrent(0)?.Animation.Name,track_time=p.AnimationState.GetCurrent(0)?.TrackTime??0,clock_time=p.ClockTime,tracks=p.Spec.tracks.Select(t=>new {track=t.track,animation=p.AnimationState.GetCurrent(t.track)?.Animation.Name}),alpha=p.Skeleton.A})});
            return 0;
        } catch(Exception) when(token.IsCancellationRequested) {
            throw new OperationCanceledException(token);
        } finally {
            if(encoder!=null){Kill(encoder);encoder.WaitForExit();encoder.Dispose();}
            if(staged!=null&&File.Exists(staged))File.Delete(staged);
            foreach(var p in parts)p.Dispose();
        }
    }
}
