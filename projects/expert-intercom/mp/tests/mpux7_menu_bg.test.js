/* MP-UX7 自验：chat 页下拉浮层不透明背景（深浅两模式实色 HEX + @media 覆盖）。
   口径：
   1. .conv-menu 基础背景 = 不透明实色 #FFFFFF（浅色），禁 rgba/透明/var 依赖
   2. @media (prefers-color-scheme: dark) 内 .conv-menu 背景 = #2C2C2E（深色实色）
   3. 浮层 z-index > 遮罩 z-index > 顶栏 z-index，防下层穿透
   4. 文字/边框/阴影等可读性属性在位
   5. MP-UX6 功能逻辑零改动：index.js/index.wxml/config.js/fmt.js 与 947ff47 完全一致 */
const fs = require('fs');
const path = require('path');

const MP = path.join(__dirname, '..');
const wxss = fs.readFileSync(path.join(MP, 'pages/chat/index.wxss'), 'utf8');

let fail = 0;
function ok(name, cond) {
  console.log((cond ? 'PASS' : 'FAIL') + ' ' + name);
  if (!cond) fail++;
}

// 抽取 .conv-menu 基础规则块（不含 @media 内的覆盖块）
const baseMatch = wxss.match(/\.conv-menu\s*\{([^}]*)\}/);
ok('C1 .conv-menu 规则存在', !!baseMatch);
const base = baseMatch ? baseMatch[1] : '';
ok('C2 基础背景 = 实色 #FFFFFF（不依赖 var 解析）', /background:\s*#FFFFFF\s*;/.test(base));
ok('C3 基础背景无 rgba/transparent', !/background:\s*(rgba|transparent)/.test(base));

// 深色覆盖块
const darkMatch = wxss.match(/@media\s*\(prefers-color-scheme:\s*dark\)\s*\{[^]*?\.conv-menu\s*\{([^}]*)\}/);
ok('C4 深色 @media 覆盖块存在', !!darkMatch);
ok('C5 深色背景 = 实色 #2C2C2E', !!darkMatch && /background:\s*#2C2C2E\s*;/.test(darkMatch[1]));
ok('C6 深色边框/阴影加强在位', !!darkMatch && /border-color:\s*#48484A/.test(darkMatch[1]) && /rgba\(0,\s*0,\s*0,\s*0\.6\)/.test(darkMatch[1]));

// z-index 层级：浮层 > 遮罩 > 顶栏
const zOf = (cls) => {
  const m = wxss.match(new RegExp('\\' + cls + '\\s*\\{([^}]*)\\}'));
  const z = m && m[1].match(/z-index:\s*(\d+)/);
  return z ? parseInt(z[1], 10) : -1;
};
ok('C7 浮层 z(201) > 遮罩 z(200) > 顶栏 z(100)',
  zOf('.conv-menu') === 201 && zOf('.menu-mask') === 200 && zOf('.topbar') === 100);

// 可读性：文字色/圆角/阴影
ok('C8 菜单项文字色 var(--fg) 在位', /\.conv-item\s*\{[^}]*color:\s*var\(--fg\)/.test(wxss));
ok('C9 浮层圆角+阴影在位', /border-radius:\s*10px/.test(base) && /box-shadow:/.test(base));

// 浅色态高亮项底色（与 #FFFFFF 浮层分层）
ok('C10 浅色 @media 内 .conv-item.on 底色 --bg2', /@media\s*\(prefers-color-scheme:\s*light\)[^]*?\.conv-item\.on\s*\{[^}]*var\(--bg2\)/.test(wxss));

process.exit(fail ? 1 : 0);
