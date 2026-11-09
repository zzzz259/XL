using System;
using System.Runtime.InteropServices;
using System.Threading;
using SFML.Graphics;

// Read the unchanged full target FBO. No texture CopyToImage (which creates
// full-frame native temporary vectors) is used by the opt-in disk path.
sealed partial class FloatCanvas {
    [DllImport("csfml-window",CallingConvention=CallingConvention.Cdecl)]
    static extern IntPtr sfContext_getFunction([MarshalAs(UnmanagedType.LPStr)] string name);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate void GetInteger(uint name,out int value);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate void PixelStore(uint name,int value);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate void BindBuffer(uint target,uint buffer);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate void ReadBuffer(uint buffer);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate uint GetError();
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate void ReadPixels(int x,int y,int w,int h,uint format,uint type,IntPtr data);
    static T Function<T>(string name) where T:Delegate {
        var address=sfContext_getFunction(name);
        if(address==IntPtr.Zero)throw new InvalidOperationException("Required GL function unavailable: "+name);
        return Marshal.GetDelegateForFunctionPointer<T>(address);
    }
    public void CaptureTarget(RenderTexture target,CancellationToken token,BlendMode? blend=null,int x0=0,int y0=0,int x1=-1,int y1=-1) {
        if(!IsDiskBacked) {
            using var image=target.Texture.CopyToImage();
            if(blend.HasValue)CaptureBlend(image.Pixels,blend.Value,x0,y0,x1,y1);else Capture(image.Pixels);
            return;
        }
        if(target.Size.X!=Width||target.Size.Y!=Height)throw new ArgumentException("Readback dimensions differ from full canvas");
        token.ThrowIfCancellationRequested();
        if(!target.SetActive(true))throw new InvalidOperationException("Could not activate full render target for readback");
        var get=Function<GetInteger>("glGetIntegerv");var pack=Function<PixelStore>("glPixelStorei");
        var bind=Function<BindBuffer>("glBindBuffer");var readBuffer=Function<ReadBuffer>("glReadBuffer");
        var error=Function<GetError>("glGetError");var read=Function<ReadPixels>("glReadPixels");
        void Checked(string phase) {uint code=error();if(code!=0)throw new InvalidOperationException($"GL readback {phase} error 0x{code:x}");}
        Checked("entry");
        uint[] names={0x0D05,0x0D02,0x0D03,0x0D04}; // alignment, row length, skip rows, skip pixels
        int[] saved=new int[names.Length];
        for(int i=0;i<names.Length;i++)get(names[i],out saved[i]);
        get(0x88ED,out int pbo);get(0x0C02,out int buffer);Checked("state query");
        var row=new byte[checked(Width*4)];
        var pin=GCHandle.Alloc(row,GCHandleType.Pinned);
        try {
            bind(0x88EB,0);pack(names[0],1);for(int i=1;i<names.Length;i++)pack(names[i],0);
            readBuffer(0x8CE0);Checked("pack setup"); // COLOR_ATTACHMENT0
            int end=y1<0?Height:Math.Min(Height,y1);
            for(int y=Math.Max(0,y0);y<end;y++) {
                token.ThrowIfCancellationRequested();
                read(0,Height-1-y,Width,1,0x1908,0x1401,pin.AddrOfPinnedObject());Checked("pixels");
                if(blend.HasValue)CaptureBlend(row,blend.Value,x0,y,x1,y+1,y);
                else CaptureRow(row,y,0);
            }
        } finally {
            pin.Free();
            for(int i=0;i<names.Length;i++)pack(names[i],saved[i]);
            bind(0x88EB,(uint)pbo);readBuffer((uint)buffer);Checked("state restore");
        }
    }
}
