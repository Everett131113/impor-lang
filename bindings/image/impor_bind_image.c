/* Impor 绑定：图像（v4.3.2）
 *
 * 为什么用 WIC 而不是 stb_image：
 *   本机没有 stb_image.h，也没有可用的 PNG 解码库；但 Windows 自带 WIC
 *   （Windows Imaging Component），能解 PNG / JPEG / BMP / GIF / TIFF，
 *   走系统解码器就不用把第三方源码塞进仓库，也不用链额外的运行库。
 *
 * 编译（64 位，与 impor run 的解释器位宽一致）：
 *   gcc -shared -O2 -o impor_bind_image.dll impor_bind_image.c -lole32 -luuid -lwindowscodecs
 *
 * 导出的都是扁平函数（Impor 的 ffi_call 只认 int / cstr）：
 *   int  impor_image_init()                     初始化 COM/WIC，返回 1/0
 *   cstr impor_image_last_error()               最近一次错误
 *   int  impor_image_load(cstr path)            解码图片，返回句柄 1..8，失败 0
 *   int  impor_image_width(int h)               宽（失败 0）
 *   int  impor_image_height(int h)              高（失败 0）
 *   int  impor_image_red/green/blue/alpha(int h, int x, int y)
 *   int  impor_image_save_png(int h, cstr path) 把句柄内容存成 PNG，返回 1/0
 *   int  impor_image_close(int h)               释放句柄，返回 1/0
 *
 * 句柄只在进程内有效；像素按 BGRA 存储，坐标原点在左上角。
 */
#include <windows.h>
#include <wincodec.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_IMGS 8
#define IMG_MAX_PIXELS 40000000L

typedef struct {
    int used;
    int w;
    int h;
    unsigned char *px; /* w*h*4，BGRA */
} ImgSlot;

static ImgSlot g_imgs[MAX_IMGS];
static IWICImagingFactory *g_factory = NULL;
static int g_com = 0;
static char g_err[512];

static void set_err(const char *msg)
{
    if (!msg) msg = "";
    strncpy(g_err, msg, sizeof(g_err) - 1);
    g_err[sizeof(g_err) - 1] = '\0';
}

const char *impor_image_last_error(void)
{
    return g_err;
}

static void to_wide(const char *src, wchar_t *dst, int cap)
{
    dst[0] = L'\0';
    if (!src) return;
    MultiByteToWideChar(CP_UTF8, 0, src, -1, dst, cap);
    dst[cap - 1] = L'\0';
}

int impor_image_init(void)
{
    HRESULT hr;
    if (g_factory) return 1;
    hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if (SUCCEEDED(hr)) g_com = 1;
    hr = CoCreateInstance(&CLSID_WICImagingFactory, NULL, CLSCTX_INPROC_SERVER,
                          &IID_IWICImagingFactory, (void **)&g_factory);
    if (FAILED(hr) || !g_factory) {
        set_err("WIC 工厂创建失败（系统缺少 windowscodecs）");
        g_factory = NULL;
        return 0;
    }
    return 1;
}

static int alloc_slot(void)
{
    int i;
    for (i = 0; i < MAX_IMGS; i++) {
        if (!g_imgs[i].used) return i;
    }
    return -1;
}

int impor_image_load(const char *path)
{
    wchar_t wpath[MAX_PATH * 2];
    IWICBitmapDecoder *dec = NULL;
    IWICBitmapFrameDecode *frame = NULL;
    IWICFormatConverter *conv = NULL;
    UINT w = 0, h = 0;
    int idx;
    HRESULT hr;

    if (!path || !path[0]) { set_err("路径为空"); return 0; }
    if (!impor_image_init()) return 0;
    to_wide(path, wpath, MAX_PATH * 2);

    hr = g_factory->lpVtbl->CreateDecoderFromFilename(g_factory, wpath,
             NULL, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &dec);
    if (FAILED(hr) || !dec) { set_err("无法识别或无法打开该图片格式"); return 0; }
    hr = dec->lpVtbl->GetFrame(dec, 0, &frame);
    if (FAILED(hr) || !frame) { set_err("图片没有可解码的帧"); goto fail; }
    hr = frame->lpVtbl->GetSize(frame, &w, &h);
    if (FAILED(hr) || w == 0 || h == 0) { set_err("图片尺寸无效"); goto fail; }
    if ((long)w * (long)h > IMG_MAX_PIXELS) { set_err("图片过大（上限 4000 万像素）"); goto fail; }

    hr = g_factory->lpVtbl->CreateFormatConverter(g_factory, &conv);
    if (FAILED(hr) || !conv) { set_err("无法创建像素格式转换器"); goto fail; }
    hr = conv->lpVtbl->Initialize(conv, (IWICBitmapSource *)frame,
             &GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone,
             NULL, 0.0, WICBitmapPaletteTypeCustom);
    if (FAILED(hr)) { set_err("无法转成 BGRA"); goto fail; }

    idx = alloc_slot();
    if (idx < 0) { set_err("同时打开的图片过多（上限 8）"); goto fail; }
    g_imgs[idx].px = (unsigned char *)malloc((size_t)w * h * 4);
    if (!g_imgs[idx].px) { set_err("内存不足"); goto fail; }
    hr = conv->lpVtbl->CopyPixels(conv, NULL, w * 4, (UINT)((size_t)w * h * 4),
                                  g_imgs[idx].px);
    if (FAILED(hr)) { free(g_imgs[idx].px); g_imgs[idx].px = NULL; set_err("读取像素失败"); goto fail; }

    g_imgs[idx].used = 1;
    g_imgs[idx].w = (int)w;
    g_imgs[idx].h = (int)h;
    conv->lpVtbl->Release(conv);
    frame->lpVtbl->Release(frame);
    dec->lpVtbl->Release(dec);
    return idx + 1;

fail:
    if (conv) conv->lpVtbl->Release(conv);
    if (frame) frame->lpVtbl->Release(frame);
    if (dec) dec->lpVtbl->Release(dec);
    return 0;
}

static ImgSlot *slot_of(int h)
{
    if (h < 1 || h > MAX_IMGS) return NULL;
    if (!g_imgs[h - 1].used) return NULL;
    return &g_imgs[h - 1];
}

int impor_image_width(int h)
{
    ImgSlot *s = slot_of(h);
    return s ? s->w : 0;
}

int impor_image_height(int h)
{
    ImgSlot *s = slot_of(h);
    return s ? s->h : 0;
}

static int channel_at(int h, int x, int y, int off)
{
    ImgSlot *s = slot_of(h);
    unsigned char *p;
    if (!s) return 0;
    if (x < 0 || y < 0 || x >= s->w || y >= s->h) return 0;
    p = s->px + ((size_t)y * s->w + x) * 4;
    return (int)p[off];
}

int impor_image_red(int h, int x, int y)   { return channel_at(h, x, y, 2); }
int impor_image_green(int h, int x, int y) { return channel_at(h, x, y, 1); }
int impor_image_blue(int h, int x, int y)  { return channel_at(h, x, y, 0); }
int impor_image_alpha(int h, int x, int y) { return channel_at(h, x, y, 3); }

int impor_image_save_png(int h, const char *path)
{
    ImgSlot *s = slot_of(h);
    wchar_t wpath[MAX_PATH * 2];
    IWICStream *stream = NULL;
    IWICBitmapEncoder *enc = NULL;
    IWICBitmapFrameEncode *frame = NULL;
    IPropertyBag2 *props = NULL;
    HRESULT hr;

    if (!s) { set_err("句柄无效"); return 0; }
    if (!path || !path[0]) { set_err("路径为空"); return 0; }
    if (!impor_image_init()) return 0;
    to_wide(path, wpath, MAX_PATH * 2);

    hr = g_factory->lpVtbl->CreateStream(g_factory, &stream);
    if (FAILED(hr) || !stream) { set_err("创建输出流失败"); goto fail; }
    hr = stream->lpVtbl->InitializeFromFilename(stream, wpath, GENERIC_WRITE);
    if (FAILED(hr)) { set_err("无法写入目标文件"); goto fail; }
    hr = g_factory->lpVtbl->CreateEncoder(g_factory, &GUID_ContainerFormatPng,
             NULL, &enc);
    if (FAILED(hr) || !enc) { set_err("创建 PNG 编码器失败"); goto fail; }
    hr = enc->lpVtbl->Initialize(enc, (IStream *)stream, WICBitmapEncoderNoCache);
    if (FAILED(hr)) { set_err("初始化编码器失败"); goto fail; }
    hr = enc->lpVtbl->CreateNewFrame(enc, &frame, &props);
    if (FAILED(hr) || !frame) { set_err("创建输出帧失败"); goto fail; }
    hr = frame->lpVtbl->Initialize(frame, props);
    if (FAILED(hr)) { set_err("初始化输出帧失败"); goto fail; }
    hr = frame->lpVtbl->SetSize(frame, (UINT)s->w, (UINT)s->h);
    if (FAILED(hr)) { set_err("设置尺寸失败"); goto fail; }
    {
        WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppBGRA;
        hr = frame->lpVtbl->SetPixelFormat(frame, &fmt);
        if (FAILED(hr)) { set_err("设置像素格式失败"); goto fail; }
    }
    hr = frame->lpVtbl->WritePixels(frame, (UINT)s->h, (UINT)(s->w * 4),
             (UINT)((size_t)s->w * s->h * 4), s->px);
    if (FAILED(hr)) { set_err("写入像素失败"); goto fail; }
    hr = frame->lpVtbl->Commit(frame);
    if (FAILED(hr)) { set_err("提交 PNG 失败"); goto fail; }
    hr = enc->lpVtbl->Commit(enc);
    if (FAILED(hr)) { set_err("提交编码器失败"); goto fail; }

    if (props) props->lpVtbl->Release(props);
    frame->lpVtbl->Release(frame);
    enc->lpVtbl->Release(enc);
    stream->lpVtbl->Release(stream);
    return 1;

fail:
    if (props) props->lpVtbl->Release(props);
    if (frame) frame->lpVtbl->Release(frame);
    if (enc) enc->lpVtbl->Release(enc);
    if (stream) stream->lpVtbl->Release(stream);
    return 0;
}

int impor_image_close(int h)
{
    ImgSlot *s = slot_of(h);
    if (!s) return 0;
    free(s->px);
    s->px = NULL;
    s->used = 0;
    s->w = 0;
    s->h = 0;
    return 1;
}
