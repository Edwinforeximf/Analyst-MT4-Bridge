package id.quickpro.analyst;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity {
    private static final int FILE_CHOOSER = 6001;
    private WebView web;
    private ValueCallback<Uri[]> fileCallback;
    private String baseUrl;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        baseUrl = getPreferences(0).getString("site_url", "");
        if (baseUrl.isEmpty()) showConfiguration(); else showWeb();
    }

    private void showConfiguration() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setPadding(42, 80, 42, 35);
        l.setBackgroundColor(Color.rgb(2,3,40));
        TextView head = new TextView(this);
        head.setText("✦ GOLD ANALYST PRO\nHubungkan aplikasi ke Railway");
        head.setTextSize(23); head.setTextColor(Color.WHITE); l.addView(head);
        EditText input = new EditText(this);
        input.setHint("https://nama-aplikasi.up.railway.app");
        input.setSingleLine(true); input.setTextColor(Color.WHITE);
        input.setHintTextColor(Color.GRAY); input.setText(baseUrl);
        l.addView(input);
        Button btn = new Button(this); btn.setText("HUBUNGKAN KE SERVER");
        btn.setOnClickListener(v -> {
            String value = input.getText().toString().trim().replaceAll("/+$", "");
            Uri uri = Uri.parse(value);
            if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getHost().isEmpty()) {
                Toast.makeText(this, "Masukkan URL HTTPS Railway yang benar", Toast.LENGTH_LONG).show();return;
            }
            baseUrl = value;
            getPreferences(0).edit().putString("site_url", baseUrl).apply();
            showWeb();
        });
        l.addView(btn);setContentView(l);
    }

    private void showWeb() {
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(2,3,40));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try { startActivityForResult(params.createIntent(), FILE_CHOOSER); return true; }
                catch (Exception ex) { fileCallback = null; return false; }
            }
        });
        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                // Only download from the configured HTTPS host; include the logged-in cookie.
                Uri target = Uri.parse(url);
                if (!"https".equals(target.getScheme()) || !target.getHost().equals(Uri.parse(baseUrl).getHost())) return;
                DownloadManager.Request req = new DownloadManager.Request(target);
                String cookie = CookieManager.getInstance().getCookie(url);
                if (cookie != null) req.addRequestHeader("Cookie", cookie);
                String filename = android.webkit.URLUtil.guessFileName(url,contentDisposition,mimeType);
                req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS,filename);
                req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                ((DownloadManager)getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(req);
                Toast.makeText(this, "Mengunduh: " + filename, Toast.LENGTH_LONG).show();
            } catch (Exception ex) { Toast.makeText(this, "Unduhan gagal: " + ex.getMessage(), Toast.LENGTH_LONG).show(); }
        });
        CookieManager.getInstance().setAcceptCookie(true);
        setContentView(web);
        web.loadUrl(baseUrl);
    }

    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data) {
        super.onActivityResult(requestCode,resultCode,data);
        if (requestCode == FILE_CHOOSER && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode,data));
            fileCallback = null;
        }
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
