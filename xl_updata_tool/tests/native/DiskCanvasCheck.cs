using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Runtime.InteropServices;
using SFML.System;
using SFML.Graphics;
using Spine.Utils;

static class DiskCanvasCheck {
    static void Check(bool value,string reason) { if(!value)throw new Exception(reason); }
    static void TmpfsCheck(string directory) {
        string path=Path.Combine(directory,"portrait-tmpfs-"+Guid.NewGuid().ToString("N")+".float");
        bool rejected=false;
        try {
            try {using var canvas=new FloatCanvas(7,9,path,2);}
            catch(IOException e) {rejected=e.Message.Contains("memory-backed");}
            Check(rejected,"memory-backed filesystem was accepted as disk backing");
            Check(!File.Exists(path),"rejected memory-backed store leaked");
        } finally {if(File.Exists(path))File.Delete(path);}
        Console.WriteLine("PASS tmpfs rejected and owned store cleaned");
    }
    static void EstimateChecks() {
        var method=typeof(FramePipeline).GetMethod("ValidateCanvasDimensions",new[]{typeof(int),typeof(int),typeof(long?),typeof(int)});
        Check(method!=null,"disk estimate must account for bounded working rows");
        long value=(long)method!.Invoke(null,new object?[]{2892,2752,100L*1024*1024,7})!;
        Check(value==8L*2892*2752+16L*2892*7+8L*2892,"unexpected disk phase allocation estimate");
        Check((long)method.Invoke(null,new object?[]{7,9,long.MaxValue,1024})! ==8L*7*9+16L*7*9+8L*7,"partial final rows overestimated");
        Check((long)method.Invoke(null,new object?[]{7,9,long.MaxValue,0})! ==40L*7*9,"default estimate changed");
        try {method.Invoke(null,new object?[]{2892,2752,1L,7});throw new Exception("insufficient estimate allowance accepted");}
        catch(TargetInvocationException e) when(e.InnerException is ArgumentException){}
        try {method.Invoke(null,new object?[]{65536,65536,long.MaxValue,7});throw new Exception("pixel cap bypassed");}
        catch(TargetInvocationException e) when(e.InnerException is ArgumentException){}
    }
    static void GraphicsChecks(string directory) {
        const int w=131,h=137;
        using var target=new RenderTexture(w,h);
        target.Clear(new Color(19,37,61,113));
        var vertices=new[]{new Vertex(new Vector2f(.37f,7.13f),new Color(211,19,97,173)),new Vertex(new Vector2f(127.7f,23.31f),new Color(11,227,79,223)),new Vertex(new Vector2f(37.41f,136.7f),new Color(41,89,241,151))};
        target.Draw(vertices,PrimitiveType.Triangles);target.Display();
        using var image=target.Texture.CopyToImage();
        foreach(int rows in new[]{1,7,128}) {
            var memory=new FloatCanvas(w,h);memory.Clear(Color.Transparent);memory.Capture(image.Pixels);
            using var disk=new FloatCanvas(w,h,Path.Combine(directory,"graphics.float"),rows);disk.Clear(Color.Transparent);
            disk.CaptureTarget(target,CancellationToken.None);
            Check(memory.Bytes(true).SequenceEqual(disk.Bytes(true)),"full-target GL row readback orientation/pixels differ");
            long before=GC.GetAllocatedBytesForCurrentThread();disk.CaptureTarget(target,CancellationToken.None);
            Check(GC.GetAllocatedBytesForCurrentThread()-before<(long)w*h*4,"GL readback allocates a full managed image");
        }
    }
    static int Main(string[] args) {
        if(args.Length>1&&args[1]=="--tmpfs") {TmpfsCheck(args[0]);return 0;}
        EstimateChecks();
        var ctor=typeof(FloatCanvas).GetConstructor(new[]{typeof(int),typeof(int),typeof(string),typeof(int)});
        Check(ctor!=null,"disk row canvas constructor is missing");
        string path=Path.Combine(args[0],"owned.float");
        foreach(int rows in new[]{1,2,4,16}) {
            var disk=(FloatCanvas)ctor!.Invoke(new object[]{7,9,path,rows});
            using var owned=(IDisposable)(object)disk;
            if(OperatingSystem.IsLinux())Check(!File.Exists(path),"Linux float store must not leave a pathname that survives hard kill");
            var memory=new FloatCanvas(7,9);
            Check(disk.Pixels.Length<=7*Math.Min(rows,9)*4,"float buffer exceeds working rows");
            foreach(var c in new[]{memory,disk}) {
                c.Clear(new Color(19,47,91,137));
                for(int pass=0;pass<3;pass++) {
                    var source=Enumerable.Range(0,7*9*4).Select(i=>(byte)((i*37+pass*29)%256)).ToArray();
                    c.Capture(source);
                    foreach(var mode in new[]{SFMLBlendMode.AdditivePma,SFMLBlendMode.MultiplyPma,SFMLBlendMode.ScreenPma,SFMLBlendMode.NormalPma})c.CaptureBlend(source,mode,1,1,6,9);
                    for(int y=8;y>=0;y--)for(int x=0;x<7;x++)c.Blend((y*7+x)*4,3.7f,.13f,1.17f,.37f,true);
                }
            }
            var length=typeof(FloatCanvas).GetProperty("BackingLength");
            Check(length!=null&&(long)length.GetValue(disk)! ==7*9*16,"float store logical size differs");
            foreach(var c in new[]{memory,disk}) {
                var emissive=new byte[7*9*4];for(int i=0;i<emissive.Length;i+=4)emissive[i]=191;
                c.Capture(emissive);
                c.CaptureBlend(emissive,new BlendMode(BlendMode.Factor.DstAlpha,BlendMode.Factor.OneMinusSrcColor,BlendMode.Equation.ReverseSubtract,BlendMode.Factor.One,BlendMode.Factor.One,BlendMode.Equation.Max));
            }
            var pointer=Marshal.AllocHGlobal(9*32);
            try {
                disk.WritePixels(pointer,32,false);var padded=new byte[9*32];Marshal.Copy(pointer,padded,0,padded.Length);
                var expected=memory.Bytes(false);for(int y=0;y<9;y++)Check(expected.AsSpan(y*28,28).SequenceEqual(padded.AsSpan(y*32,28)),"padded output stride differs");
            } finally {Marshal.FreeHGlobal(pointer);}
            Check(memory.Bytes(true).SequenceEqual(disk.Bytes(true)),"PMA differs");
            Check(memory.Bytes(false).SequenceEqual(disk.Bytes(false)),"straight RGBA differs");
            Check(BitConverter.SingleToInt32Bits(memory.Peak)==BitConverter.SingleToInt32Bits(disk.Peak),"float peak differs");
            owned.Dispose();Check(!File.Exists(path),"owned backing file leaked");
        }
        var cancellable=typeof(FloatCanvas).GetMethod("Clear",new[]{typeof(Color),typeof(CancellationToken)});
        Check(cancellable!=null,"clear must observe cancellation during bounded disk passes");
        foreach(int invalid in new[]{-1,1025}) {
            try { FloatCanvas.ValidateWorkingRows(2,new Settings{png_working_rows=invalid});throw new Exception("invalid rows accepted"); }catch(ArgumentException){}
        }
        foreach(var setting in new[]{new Settings{png_working_rows=1,format="Jpg"},new Settings{png_working_rows=1,format="Mp4"}}) {
            try {FloatCanvas.ValidateWorkingRows(2,setting);throw new Exception("invalid format accepted");}catch(ArgumentException){}
        }
        try {FloatCanvas.ValidateWorkingRows(1,new Settings{png_working_rows=1});throw new Exception("schema1 accepted");}catch(ArgumentException){}
        using(var canceled=(IDisposable)ctor!.Invoke(new object[]{7,9,path,2})) {
            using var cts=new CancellationTokenSource();cts.Cancel();
            try {cancellable!.Invoke(canceled,new object[]{Color.Transparent,cts.Token});throw new Exception("canceled clear completed");}
            catch(TargetInvocationException e) when(e.InnerException is OperationCanceledException){}
        }
        Check(!File.Exists(path),"canceled canvas file leaked");
        File.WriteAllText(path,"sentinel");
        try {ctor!.Invoke(new object[]{7,9,path,2});throw new Exception("existing backing file overwritten");}
        catch(TargetInvocationException e) when(e.InnerException is IOException){}
        Check(File.ReadAllText(path)=="sentinel","unowned file modified");File.Delete(path);
        if(args.Length>1&&args[1]=="--graphics")GraphicsChecks(args[0]);
        Console.WriteLine("PASS disk row float exact RGBA, peak, bounds and cleanup");return 0;
    }
}
