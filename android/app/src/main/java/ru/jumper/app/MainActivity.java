package ru.jumper.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Color;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Jumper для Android: находит ПК с запущенным Jumper в домашней сети
 * и открывает его страницу. Поиск — широковещательный UDP-запрос на порт 8766.
 */
public class MainActivity extends Activity {

    private static final int HTTP_PORT = 8765;
    private static final int DISCOVERY_PORT = 8766;
    private static final int REQ_FILES = 1;
    private static final String PREFS = "jumper";
    private static final String KEY_HOST = "host";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private WebView web;
    private View overlay;
    private TextView statusText;
    private ProgressBar spinner;
    private LinearLayout buttons;
    private ValueCallback<Uri[]> fileCallback;
    private volatile String host;          // "192.168.1.5:8765"
    private volatile boolean searching;
    private String pendingShare;            // текст, которым поделились из другого приложения

    // ---------- жизненный цикл ----------

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        buildUi();
        pendingShare = sharedText(getIntent());
        host = prefs().getString(KEY_HOST, null);
        discover();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String text = sharedText(intent);
        if (text != null) {
            if (host != null && !searching) sendShared(text);
            else pendingShare = text;
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        // После сна телефона ПК мог получить новый адрес — тихо проверяем
        final String h = host;
        if (h == null || searching || overlay.getVisibility() == View.VISIBLE) return;
        new Thread(() -> {
            if (!ping(h)) ui.post(this::discover);
        }).start();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    // ---------- интерфейс ----------

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics());
    }

    private boolean isNight() {
        return (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                == Configuration.UI_MODE_NIGHT_YES;
    }

    private void buildUi() {
        int bg = isNight() ? Color.parseColor("#0F131B") : Color.parseColor("#EEF1F6");
        int ink = isNight() ? Color.parseColor("#E4E8F0") : Color.parseColor("#18202E");

        FrameLayout root = new FrameLayout(this);
        root.setFitsSystemWindows(true);
        root.setBackgroundColor(bg);

        web = new WebView(this);
        web.setBackgroundColor(bg);
        setupWebView();
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackgroundColor(bg);
        box.setPadding(dp(32), dp(32), dp(32), dp(32));
        box.setClickable(true);

        TextView title = new TextView(this);
        title.setText("JUMPER");
        title.setTextSize(28);
        title.setLetterSpacing(0.08f);
        title.setTextColor(ink);
        title.setGravity(Gravity.CENTER);
        box.addView(title);

        spinner = new ProgressBar(this);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(dp(40), dp(40));
        sp.topMargin = dp(24);
        box.addView(spinner, sp);

        statusText = new TextView(this);
        statusText.setTextSize(16);
        statusText.setTextColor(ink);
        statusText.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams tp = new LinearLayout.LayoutParams(-2, -2);
        tp.topMargin = dp(16);
        box.addView(statusText, tp);

        buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.VERTICAL);
        buttons.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(-2, -2);
        bp.topMargin = dp(24);
        box.addView(buttons, bp);

        Button retry = new Button(this);
        retry.setText("Искать снова");
        retry.setOnClickListener(v -> discover());
        buttons.addView(retry);

        Button manual = new Button(this);
        manual.setText("Ввести адрес вручную");
        manual.setOnClickListener(v -> askAddress());
        buttons.addView(manual);

        overlay = box;
        root.addView(box, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
    }

    private void showSearching(String msg) {
        overlay.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.VISIBLE);
        buttons.setVisibility(View.GONE);
        statusText.setText(msg);
    }

    private void showNotFound() {
        overlay.setVisibility(View.VISIBLE);
        spinner.setVisibility(View.GONE);
        buttons.setVisibility(View.VISIBLE);
        statusText.setText("ПК не найден.\n\nПроверьте, что Jumper запущен на компьютере и телефон подключён к тому же Wi-Fi.");
    }

    private void askAddress() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("192.168.1.5");
        if (host != null) input.setText(host.replace(":" + HTTP_PORT, ""));
        FrameLayout wrap = new FrameLayout(this);
        wrap.setPadding(dp(20), dp(8), dp(20), 0);
        wrap.addView(input);
        new AlertDialog.Builder(this)
                .setTitle("Адрес ПК")
                .setMessage("Его показывает окно Jumper на компьютере")
                .setView(wrap)
                .setPositiveButton("Подключиться", (d, w) -> {
                    String a = input.getText().toString().trim()
                            .replaceFirst("^https?://", "").replaceAll("/.*$", "");
                    if (a.isEmpty()) return;
                    if (!a.contains(":")) a = a + ":" + HTTP_PORT;
                    final String candidate = a;
                    showSearching("Подключаюсь к " + candidate + "…");
                    new Thread(() -> {
                        boolean ok = ping(candidate);
                        ui.post(() -> {
                            if (ok) connected(candidate);
                            else {
                                showNotFound();
                                toast("По адресу " + candidate + " Jumper не отвечает");
                            }
                        });
                    }).start();
                })
                .setNegativeButton("Отмена", null)
                .show();
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show();
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    // ---------- поиск ПК ----------

    private void discover() {
        if (searching) return;
        searching = true;
        showSearching("Ищу компьютер с Jumper…");
        final String saved = host;
        new Thread(() -> {
            String found = null;
            if (saved != null && ping(saved)) found = saved;
            if (found == null) found = broadcastSearch();
            final String result = found;
            ui.post(() -> {
                searching = false;
                if (result != null) connected(result);
                else showNotFound();
            });
        }).start();
    }

    private void connected(String h) {
        host = h;
        prefs().edit().putString(KEY_HOST, h).apply();
        overlay.setVisibility(View.GONE);
        web.loadUrl("http://" + h + "/");
        if (pendingShare != null) {
            sendShared(pendingShare);
            pendingShare = null;
        }
    }

    /** Отправляет UDP-запрос во все локальные сети и ждёт ответа «JUMPER_HERE <порт>». */
    private String broadcastSearch() {
        WifiManager.MulticastLock lock = null;
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifi != null) {
                lock = wifi.createMulticastLock("jumper");
                lock.setReferenceCounted(false);
                lock.acquire();
            }
        } catch (Exception ignored) {
        }
        try (DatagramSocket sock = new DatagramSocket()) {
            sock.setBroadcast(true);
            sock.setSoTimeout(700);
            byte[] ask = "JUMPER_DISCOVER".getBytes(StandardCharsets.UTF_8);
            List<InetAddress> targets = broadcastAddresses();
            for (int round = 0; round < 5; round++) {
                for (InetAddress t : targets) {
                    try {
                        sock.send(new DatagramPacket(ask, ask.length, t, DISCOVERY_PORT));
                    } catch (Exception ignored) {
                    }
                }
                long until = System.currentTimeMillis() + 700;
                while (System.currentTimeMillis() < until) {
                    byte[] buf = new byte[256];
                    DatagramPacket p = new DatagramPacket(buf, buf.length);
                    try {
                        sock.receive(p);
                    } catch (SocketTimeoutException e) {
                        break;
                    }
                    String reply = new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8).trim();
                    if (reply.startsWith("JUMPER_HERE")) {
                        int port = HTTP_PORT;
                        String[] parts = reply.split(" ");
                        if (parts.length > 1) {
                            try {
                                port = Integer.parseInt(parts[1]);
                            } catch (NumberFormatException ignored) {
                            }
                        }
                        return p.getAddress().getHostAddress() + ":" + port;
                    }
                }
            }
        } catch (Exception ignored) {
        } finally {
            if (lock != null && lock.isHeld()) lock.release();
        }
        return null;
    }

    private List<InetAddress> broadcastAddresses() {
        List<InetAddress> list = new ArrayList<>();
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (InterfaceAddress ia : ni.getInterfaceAddresses()) {
                    InetAddress b = ia.getBroadcast();
                    if (b instanceof Inet4Address && !list.contains(b)) list.add(b);
                }
            }
        } catch (Exception ignored) {
        }
        try {
            InetAddress all = InetAddress.getByName("255.255.255.255");
            if (!list.contains(all)) list.add(all);
        } catch (Exception ignored) {
        }
        return list;
    }

    private boolean ping(String h) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://" + h + "/api/clips").openConnection();
            c.setConnectTimeout(1500);
            c.setReadTimeout(2000);
            return c.getResponseCode() == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    // ---------- «Поделиться» из других приложений ----------

    private String sharedText(Intent intent) {
        if (intent != null && Intent.ACTION_SEND.equals(intent.getAction())) {
            CharSequence t = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
            if (t != null && t.length() > 0) return t.toString();
        }
        return null;
    }

    private void sendShared(final String text) {
        final String h = host;
        new Thread(() -> {
            boolean ok = false;
            HttpURLConnection c = null;
            try {
                JSONObject body = new JSONObject();
                body.put("text", text);
                body.put("device", "Телефон");
                byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
                c = (HttpURLConnection) new URL("http://" + h + "/api/clips").openConnection();
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setConnectTimeout(3000);
                c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                c.setFixedLengthStreamingMode(data.length);
                try (OutputStream os = c.getOutputStream()) {
                    os.write(data);
                }
                ok = c.getResponseCode() < 300;
            } catch (Exception ignored) {
            } finally {
                if (c != null) c.disconnect();
            }
            final boolean sent = ok;
            ui.post(() -> {
                toast(sent ? "Отправлено на ПК" : "Не удалось отправить на ПК");
                if (sent) web.evaluateJavascript("typeof poll==='function'&&poll()", null);
            });
        }).start();
    }

    // ---------- WebView ----------

    private void setupWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setTextZoom(100);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUserAgentString(s.getUserAgentString() + " JumperApp/1.0");

        web.addJavascriptInterface(new Bridge(), "JumperApp");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                String h = host;
                if (h != null && h.equals(u.getHost() + ":" + (u.getPort() == -1 ? 80 : u.getPort()))) {
                    return false; // страницы самого Jumper открываем внутри
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) {
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) ui.post(MainActivity.this::discover);
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                }
                try {
                    startActivityForResult(Intent.createChooser(pick, "Выберите файл"), REQ_FILES);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) ->
                download(url, contentDisposition, mimeType));
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQ_FILES || fileCallback == null) return;
        Uri[] result = null;
        if (resultCode == RESULT_OK && data != null) {
            ClipData clip = data.getClipData();
            if (clip != null && clip.getItemCount() > 0) {
                result = new Uri[clip.getItemCount()];
                for (int i = 0; i < clip.getItemCount(); i++) result[i] = clip.getItemAt(i).getUri();
            } else if (data.getData() != null) {
                result = new Uri[]{data.getData()};
            }
        }
        fileCallback.onReceiveValue(result);
        fileCallback = null;
    }

    // ---------- скачивание файлов ----------

    private static String fileNameFrom(String url, String disposition, String mime) {
        if (disposition != null) {
            Matcher m = Pattern.compile("filename\\*=UTF-8''([^;]+)", Pattern.CASE_INSENSITIVE).matcher(disposition);
            if (m.find()) {
                try {
                    return URLDecoder.decode(m.group(1).trim(), "UTF-8");
                } catch (Exception ignored) {
                }
            }
        }
        return URLUtil.guessFileName(url, disposition, mime);
    }

    private void download(final String url, String disposition, String mime) {
        final String name = fileNameFrom(url, disposition, mime);

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            // Старые Android: системный загрузчик, результат виден в приложении «Загрузки»
            try {
                DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
                r.setTitle(name);
                r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
                toast("Скачиваю " + name + "…");
            } catch (Exception e) {
                toast("Не удалось скачать файл");
            }
            return;
        }

        toast("Скачиваю " + name + "…");
        new Thread(() -> {
            boolean ok = false;
            Uri item = null;
            ContentResolver cr = getContentResolver();
            HttpURLConnection c = null;
            try {
                ContentValues v = new ContentValues();
                v.put(MediaStore.Downloads.DISPLAY_NAME, name);
                v.put(MediaStore.Downloads.MIME_TYPE, mime != null && !mime.isEmpty() ? mime : "application/octet-stream");
                v.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Jumper");
                v.put(MediaStore.Downloads.IS_PENDING, 1);
                item = cr.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                if (item == null) throw new IllegalStateException("no uri");

                c = (HttpURLConnection) new URL(url).openConnection();
                c.setConnectTimeout(5000);
                c.setReadTimeout(30000);
                try (InputStream in = c.getInputStream(); OutputStream out = cr.openOutputStream(item)) {
                    if (out == null) throw new IllegalStateException("no stream");
                    byte[] buf = new byte[256 * 1024];
                    int n;
                    while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                }
                ContentValues done = new ContentValues();
                done.put(MediaStore.Downloads.IS_PENDING, 0);
                cr.update(item, done, null, null);
                ok = true;
            } catch (Exception e) {
                if (item != null) {
                    try {
                        cr.delete(item, null, null);
                    } catch (Exception ignored) {
                    }
                }
            } finally {
                if (c != null) c.disconnect();
            }
            final boolean saved = ok;
            ui.post(() -> toast(saved ? "Сохранено: Загрузки/Jumper/" + name : "Не удалось скачать " + name));
        }).start();
    }

    // ---------- мост для страницы: буфер обмена ----------

    private class Bridge {
        @JavascriptInterface
        public void copy(final String text) {
            ui.post(() -> {
                ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                if (cm != null) cm.setPrimaryClip(ClipData.newPlainText("Jumper", text));
            });
        }

        @JavascriptInterface
        public String paste() {
            final String[] out = {null};
            final CountDownLatch latch = new CountDownLatch(1);
            ui.post(() -> {
                try {
                    ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                    if (cm != null && cm.hasPrimaryClip() && cm.getPrimaryClip() != null
                            && cm.getPrimaryClip().getItemCount() > 0) {
                        CharSequence t = cm.getPrimaryClip().getItemAt(0).coerceToText(MainActivity.this);
                        out[0] = t == null ? "" : t.toString();
                    } else {
                        out[0] = "";
                    }
                } catch (Exception e) {
                    out[0] = null;
                } finally {
                    latch.countDown();
                }
            });
            try {
                latch.await(2, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
            }
            return out[0];
        }
    }
}
