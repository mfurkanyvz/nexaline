package com.nexaline.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Notification;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ResolveInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.provider.MediaStore;
import android.media.AudioAttributes;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.GeolocationPermissions;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import androidx.core.app.ActivityCompat;
import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;
import androidx.core.content.FileProvider;

import java.io.File;
import java.io.IOException;
import java.util.List;

public class MainActivity extends Activity {
    private static final int PERMISSION_REQUEST = 10;
    private static final int FILE_REQUEST = 11;
    private static final String CHANNEL_ID = "nexaline_messages_v2";
    private static final String CALL_CHANNEL_ID = "nexaline_calls_v1";
    private static final String PREFS_NAME = "nexaline_app";
    private static final String WEBVIEW_RESET_KEY = "webview_reset_version";
    private static final String WEBVIEW_RESET_VERSION = "2026-06-04-login-cache-fix";
    private WebView webView;
    private ValueCallback<Uri[]> fileCallback;
    private Uri cameraOutputUri;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureIncomingCallWindow(getIntent());
        createNotificationChannel();
        requestAppPermissions();

        webView = new WebView(this);
        webView.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        webView.setLayerType(WebView.LAYER_TYPE_HARDWARE, null);
        webView.setOverScrollMode(WebView.OVER_SCROLL_NEVER);
        webView.setScrollBarStyle(WebView.SCROLLBARS_INSIDE_OVERLAY);
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setTextZoom(100);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            settings.setOffscreenPreRaster(true);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            settings.setSafeBrowsingEnabled(true);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
            CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);
        }
        CookieManager.getInstance().setAcceptCookie(true);

        webView.addJavascriptInterface(new NativeBridge(), "NexaLineNative");
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String scheme = uri.getScheme();
                if ("tel".equals(scheme) || "mailto".equals(scheme) || "geo".equals(scheme)) {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    return true;
                }
                return false;
            }

            @Override
            public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                if (webView != null) {
                    webView.destroy();
                }
                recreateWebView();
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                view.evaluateJavascript(
                    "document.documentElement.classList.add('nexaline-native-android');"
                        + "document.documentElement.style.webkitTapHighlightColor='transparent';",
                    null
                );
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showConnectionError();
                }
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> request.grant(request.getResources()));
            }

            @Override
            public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                callback.invoke(origin, true, false);
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) {
                    fileCallback.onReceiveValue(null);
                }
                fileCallback = callback;
                try {
                    if (params.isCaptureEnabled()) {
                        startActivityForResult(createCameraIntent(params.getAcceptTypes()), FILE_REQUEST);
                    } else {
                        startActivityForResult(createGalleryIntent(params), FILE_REQUEST);
                    }
                } catch (Exception error) {
                    cameraOutputUri = null;
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
        webView.setDownloadListener((url, userAgent, contentDisposition, mimetype, contentLength) -> {
            try {
                DownloadManager.Request download = new DownloadManager.Request(Uri.parse(url));
                download.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                download.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, Uri.parse(url).getLastPathSegment());
                download.allowScanningByMediaScanner();
                DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
                manager.enqueue(download);
                Toast.makeText(this, "İndirme başlatıldı", Toast.LENGTH_SHORT).show();
            } catch (Exception error) {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            }
        });

        if (resetWebViewStorageIfNeeded()) {
            webView.loadUrl(BuildConfig.NEXALINE_URL + "/reset-client?apk=" + WEBVIEW_RESET_VERSION);
        } else {
            webView.loadUrl(BuildConfig.NEXALINE_URL + "?apk=" + WEBVIEW_RESET_VERSION);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        configureIncomingCallWindow(intent);
        if (webView != null && intent != null && intent.getBooleanExtra("nexaline_call", false)) {
            webView.evaluateJavascript("window.dispatchEvent(new Event('nexaline:incoming-call-open'));", null);
        }
    }

    private void configureIncomingCallWindow(Intent intent) {
        if (intent == null || !intent.getBooleanExtra("nexaline_call", false)) {
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        } else {
            getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                    | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                    | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            );
        }
    }

    private Intent createGalleryIntent(WebChromeClient.FileChooserParams params) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType(resolveMimeType(params.getAcceptTypes()));
        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE);
        String[] mimeTypes = resolveMimeTypes(params.getAcceptTypes());
        if (mimeTypes.length > 1) {
            intent.putExtra(Intent.EXTRA_MIME_TYPES, mimeTypes);
        }
        return intent;
    }

    private Intent createCameraIntent(String[] acceptTypes) throws IOException {
        boolean videoOnly = false;
        if (acceptTypes != null && acceptTypes.length == 1) {
            videoOnly = acceptTypes[0] != null && acceptTypes[0].startsWith("video/");
        }
        if (videoOnly) {
            cameraOutputUri = null;
            return new Intent(MediaStore.ACTION_VIDEO_CAPTURE);
        }

        File directory = new File(getCacheDir(), "camera");
        if (!directory.exists() && !directory.mkdirs()) {
            throw new IOException("Camera cache directory could not be created");
        }
        File output = File.createTempFile("nexaline-camera-", ".jpg", directory);
        cameraOutputUri = FileProvider.getUriForFile(this, BuildConfig.APPLICATION_ID + ".fileprovider", output);

        Intent intent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
        intent.putExtra(MediaStore.EXTRA_OUTPUT, cameraOutputUri);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        List<ResolveInfo> cameraApps = getPackageManager().queryIntentActivities(intent, PackageManager.MATCH_DEFAULT_ONLY);
        for (ResolveInfo cameraApp : cameraApps) {
            grantUriPermission(
                cameraApp.activityInfo.packageName,
                cameraOutputUri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            );
        }
        return intent;
    }

    private String resolveMimeType(String[] acceptTypes) {
        String[] mimeTypes = resolveMimeTypes(acceptTypes);
        return mimeTypes.length == 1 ? mimeTypes[0] : "*/*";
    }

    private String[] resolveMimeTypes(String[] acceptTypes) {
        if (acceptTypes == null || acceptTypes.length == 0) {
            return new String[0];
        }
        return java.util.Arrays.stream(acceptTypes)
            .filter(type -> type != null && !type.trim().isEmpty())
            .flatMap(type -> java.util.Arrays.stream(type.split(",")))
            .map(String::trim)
            .filter(type -> !type.isEmpty())
            .distinct()
            .toArray(String[]::new);
    }

    private boolean resetWebViewStorageIfNeeded() {
        SharedPreferences preferences = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        if (WEBVIEW_RESET_VERSION.equals(preferences.getString(WEBVIEW_RESET_KEY, ""))) {
            return false;
        }

        try {
            webView.clearCache(true);
            webView.clearHistory();
            WebStorage.getInstance().deleteAllData();
            CookieManager cookieManager = CookieManager.getInstance();
            cookieManager.removeAllCookies(null);
            cookieManager.removeSessionCookies(null);
            cookieManager.flush();
        } catch (Exception ignored) {
        }

        preferences.edit().putString(WEBVIEW_RESET_KEY, WEBVIEW_RESET_VERSION).apply();
        return true;
    }

    private void showConnectionError() {
        String html = "<!doctype html><html><head><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            + "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#070a10;color:#eef4ff;font-family:Arial,sans-serif;padding:22px;text-align:center}"
            + ".box{max-width:420px}.mark{width:58px;height:58px;margin:0 auto 18px;border-radius:16px;background:#202633;display:grid;place-items:center;font-weight:800}"
            + "p{color:#95a0b5;line-height:1.45}button{border:0;border-radius:8px;padding:12px 16px;background:#2f8cff;color:#fff;font:inherit}</style></head>"
            + "<body><div class=\"box\"><div class=\"mark\">NL</div><h2>NexaLine açılamadı</h2>"
            + "<p>Telefonun ve sunucunun aynı ağa bağlı olduğundan emin ol. Sunucu adresi: "
            + BuildConfig.NEXALINE_URL
            + "</p><button onclick=\"location.href='"
            + BuildConfig.NEXALINE_URL
            + "'\">Tekrar dene</button></div></body></html>";
        webView.loadDataWithBaseURL(BuildConfig.NEXALINE_URL, html, "text/html", "UTF-8", null);
    }

    private void recreateWebView() {
        runOnUiThread(() -> {
            webView = null;
            onCreate(null);
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.onResume();
            webView.evaluateJavascript("window.dispatchEvent(new Event('nexaline:resume')); if (window.loadBootstrap) { window.loadBootstrap().catch(function(){}); }", null);
        }
    }

    @Override
    protected void onPause() {
        if (webView != null) {
            webView.evaluateJavascript("window.dispatchEvent(new Event('nexaline:pause'));", null);
            webView.onPause();
        }
        super.onPause();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_REQUEST && fileCallback != null) {
            Uri[] result;
            if (resultCode == RESULT_OK && cameraOutputUri != null && (data == null || data.getData() == null)) {
                result = new Uri[] { cameraOutputUri };
            } else {
                result = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
            }
            fileCallback.onReceiveValue(result);
            fileCallback = null;
            cameraOutputUri = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            moveTaskToBack(true);
        }
    }

    private void requestAppPermissions() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            return;
        }
        String[] permissions = new String[] { Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO, Manifest.permission.ACCESS_FINE_LOCATION };
        ActivityCompat.requestPermissions(this, permissions, PERMISSION_REQUEST);
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "NexaLine", NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("NexaLine mesaj ve arama bildirimleri");
        channel.enableVibration(true);
        channel.setVibrationPattern(new long[] { 0, 180, 100, 220 });
        channel.setSound(Settings.System.DEFAULT_NOTIFICATION_URI, null);
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(channel);

        AudioAttributes callAudio = new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build();
        NotificationChannel callChannel = new NotificationChannel(CALL_CHANNEL_ID, "NexaLine aramaları", NotificationManager.IMPORTANCE_HIGH);
        callChannel.setDescription("Gelen NexaLine sesli ve görüntülü aramaları");
        callChannel.enableVibration(true);
        callChannel.setVibrationPattern(new long[] { 0, 500, 250, 500, 250, 800 });
        callChannel.setSound(Settings.System.DEFAULT_RINGTONE_URI, callAudio);
        callChannel.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
        manager.createNotificationChannel(callChannel);
    }

    private void showNotification(String title, String body, String tag) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
            && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        Intent intent = new Intent(this, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        boolean isCall = tag != null && tag.startsWith("call");
        intent.putExtra("nexaline_call", isCall);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            this,
            isCall ? 1 : 0,
            intent,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, isCall ? CALL_CHANNEL_ID : CHANNEL_ID)
            .setSmallIcon(android.R.drawable.sym_action_chat)
            .setContentTitle(title)
            .setContentText(body)
            .setAutoCancel(true)
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .setCategory(isCall ? NotificationCompat.CATEGORY_CALL : NotificationCompat.CATEGORY_MESSAGE)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(pendingIntent);
        if (isCall) {
            builder
                .setFullScreenIntent(pendingIntent, true)
                .setOngoing(true)
                .setAutoCancel(false)
                .setTimeoutAfter(60_000)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC);
        }
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        manager.notify(Math.abs((tag == null ? title : tag).hashCode()), builder.build());
    }

    public class NativeBridge {
        @JavascriptInterface
        public boolean isNativeAndroid() {
            return true;
        }

        @JavascriptInterface
        public boolean canNotify() {
            return Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU
                || ContextCompat.checkSelfPermission(MainActivity.this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
        }

        @JavascriptInterface
        public void requestNotificationPermission() {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !canNotify()) {
                runOnUiThread(() -> ActivityCompat.requestPermissions(
                    MainActivity.this,
                    new String[] { Manifest.permission.POST_NOTIFICATIONS },
                    PERMISSION_REQUEST
                ));
            }
        }

        @JavascriptInterface
        public void notify(String title, String body, String tag) {
            runOnUiThread(() -> showNotification(title, body, tag));
        }

        @JavascriptInterface
        public void openSettings() {
            runOnUiThread(() -> {
                Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
                intent.setData(Uri.parse("package:" + getPackageName()));
                startActivity(intent);
            });
        }
    }
}
