(function () {
  "use strict";
  const { React, components } = window.__HERMES_PLUGIN_SDK__;
  const { Card, CardHeader, CardTitle, CardContent } = components;
  const e = React.createElement;
  function Page() {
    const [data, setData] = React.useState(null);
    const [busy, setBusy] = React.useState(false);
    async function refresh() {
      setBusy(true);
      try {
        const r = await fetch("/api/plugins/hub-antigravity/status", {credentials: "same-origin"});
        setData(r.ok ? await r.json() : {error_code: "HTTP " + r.status});
      } catch (_) { setData({error_code: "host_unreachable"}); }
      finally { setBusy(false); }
    }
    React.useEffect(() => { refresh(); }, []);
    return e(Card, null, e(CardHeader, null, e(CardTitle, null, "AI Memory Hub · Antigravity")),
      e(CardContent, {className: "space-y-3"},
        e("p", null, "Hermes quản lý Chat, Kanban, MCP, Skills và Session. Hub chỉ mở rộng kết nối Windows."),
        e("button", {type:"button", disabled:busy, onClick:refresh, className:"rounded border px-3 py-2"}, busy ? "Đang kiểm tra..." : "Kiểm tra Antigravity"),
        data && (data.error_code ? e("p", null, "Kết nối: " + data.error_code) :
          e("dl", {className:"space-y-2"}, ...[
            ["CLI",data.installed ? "Đã phát hiện" : "Chưa phát hiện"], ["Phiên bản",data.version || "Không có dữ liệu"],
            ["Thực thi",data.enabled && data.configured ? "Đã cấu hình thử nghiệm" : "Tắt / chưa cấu hình"],
            ["Model",data.model || "Chưa chọn"], ["Quota", "Không có dữ liệu"], ["Xác thực model","Chưa kiểm chứng"]
          ].flatMap(([k,v])=>[e("dt",{key:k,className:"font-medium"},k),e("dd",{key:k+"v"},v)]))),
        e("p", {className:"text-sm text-muted-foreground"}, "Dùng Chat để gọi hub_antigravity_status hoặc hub_antigravity_analyze sau khi bật thử nghiệm. Chưa hỗ trợ sửa code, tự retry hoặc external Kanban worker.")));
  }
  window.__HERMES_PLUGINS__.register("hub-antigravity", Page);
})();
