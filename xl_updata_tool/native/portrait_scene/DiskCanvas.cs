using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

// One exclusively owned raw float file and one row window. Width/Height always
// describe the full projection; only destination storage indices are local.
sealed partial class FloatCanvas : IDisposable {
    readonly FileStream? store;
    readonly string? storePath;
    readonly int workingRows;
    int firstRow=-1, loadedRows;
    bool dirty,disposed;
    public bool IsDiskBacked => store!=null;
    public long BackingLength => store?.Length??0;
    [DllImport("libc",SetLastError=false)] static extern int posix_fadvise(int fd,long offset,long length,int advice);
    [DllImport("libc",SetLastError=true)] static extern int fstatfs(int fd,IntPtr buffer);
    void ValidateBackingFilesystem() {
        if(!OperatingSystem.IsLinux())return;
        // Linux statfs starts with a native long f_type. Reserve more than the
        // complete native struct on supported Linux ABIs, without imposing a
        // managed layout on its architecture-dependent counters and padding.
        var buffer=Marshal.AllocHGlobal(256);
        try {
            if(fstatfs(store!.SafeFileHandle.DangerousGetHandle().ToInt32(),buffer)!=0)
                throw new IOException($"Could not inspect owned float store filesystem: errno {Marshal.GetLastPInvokeError()}");
            uint type=unchecked((uint)Marshal.ReadIntPtr(buffer).ToInt64());
            if(type is 0x01021994 or 0x858458f6)
                throw new IOException($"Owned float store requires disk backing; memory-backed filesystem 0x{type:x} is unsupported");
        } finally {Marshal.FreeHGlobal(buffer);}
    }
    public FloatCanvas(int width,int height,string path,int rows) {
        if(width<=0||height<=0||rows<1||rows>1024||(long)width*height>FramePipeline.MaxCanvasPixels)throw new ArgumentException("Invalid disk canvas dimensions/rows");
        Width=width;Height=height;workingRows=Math.Min(rows,height);
        Pixels=new float[checked(width*workingRows*4)];
        storePath=Path.GetFullPath(path);
        // CreateNew: neither overwrite nor later delete a file belonging to another task.
        store=new FileStream(storePath,FileMode.CreateNew,FileAccess.ReadWrite,FileShare.None,1,FileOptions.RandomAccess);
        try {
            ValidateBackingFilesystem();
            store.SetLength(checked((long)width*height*16));
            // Keep only the owned open inode on Linux. SIGKILL/OOM then closes
            // the final handle in the kernel, without relying on managed finally.
            if(OperatingSystem.IsLinux()){File.Delete(storePath);storePath=null;}
        }
        catch {store.Dispose();if(storePath!=null)File.Delete(storePath);throw;}
    }
    void Evict(long offset,long length) {
        if(!OperatingSystem.IsLinux())return;
        int result=posix_fadvise(store!.SafeFileHandle.DangerousGetHandle().ToInt32(),offset,length,4);
        if(result!=0)throw new IOException($"Owned float store posix_fadvise(DONTNEED) failed: {result}");
    }
    void FlushRows() {
        if(store==null||firstRow<0)return;
        long offset=checked((long)firstRow*Width*16),length=checked((long)loadedRows*Width*16);
        if(dirty) {
            store.Position=offset;
            store.Write(MemoryMarshal.AsBytes(Pixels.AsSpan(0,checked(loadedRows*Width*4))));
            store.Flush(true);dirty=false;
        }
        // Include boundary pages: they contain no writable mappings and all writes
        // to this exclusively owned file were fsynced. Never advise unrelated files.
        long page=Environment.SystemPageSize;
        long start=offset/page*page,end=checked((offset+length+page-1)/page*page);
        Evict(start,end-start);
    }
    int RowIndex(int y,bool writing=true) {
        if(disposed)throw new ObjectDisposedException(nameof(FloatCanvas));
        if(y<0||y>=Height)throw new ArgumentOutOfRangeException(nameof(y));
        if(store==null)return checked(y*Width*4);
        if(firstRow<0||y<firstRow||y>=firstRow+loadedRows) {
            FlushRows();firstRow=y/workingRows*workingRows;loadedRows=Math.Min(workingRows,Height-firstRow);
            store.Position=checked((long)firstRow*Width*16);
            store.ReadExactly(MemoryMarshal.AsBytes(Pixels.AsSpan(0,checked(loadedRows*Width*4))));
        }
        if(writing)dirty=true;
        return checked((y-firstRow)*Width*4);
    }
    public void ForEachWindow(Action<int,int> draw,CancellationToken token) {
        int rows=IsDiskBacked?workingRows:Height;
        for(int y=0;y<Height;y+=rows) {token.ThrowIfCancellationRequested();draw(y,Math.Min(Height,y+rows));}
    }
    public void Dispose() {
        if(disposed)return;
        disposed=true;
        try {store?.Dispose();}finally {if(storePath!=null)File.Delete(storePath);}
    }
    public static void ValidateWorkingRows(int schema,Settings settings) {
        if(settings.png_working_rows<0||settings.png_working_rows>1024||
           (settings.png_working_rows!=0&&(schema!=2||!settings.format.Equals("Png",StringComparison.OrdinalIgnoreCase))))
            throw new ArgumentException("png_working_rows requires schema2 PNG and must be in 0..1024");
    }
}
