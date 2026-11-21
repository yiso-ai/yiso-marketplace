const FALLBACK_REPO = "yiso-ai/yiso-marketplace";
const PLUGIN_COMMAND = "/yisolib:library";

function detectRepo() {
  const requested = new URLSearchParams(window.location.search).get("repo");
  if (requested && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(requested)) {
    return requested;
  }

  const host = window.location.hostname;
  const segments = window.location.pathname.split("/").filter(Boolean);
  if (host.endsWith(".github.io")) {
    const owner = host.slice(0, -".github.io".length);
    if (owner && segments.length > 0) {
      return `${owner}/${segments[0]}`;
    }
  }

  return FALLBACK_REPO;
}

const repo = detectRepo();
const repoUrl = `https://github.com/${repo}`;
const archiveUrl = `https://github.com/${repo}/archive/refs/heads/main.tar.gz`;

const commands = {
  "install-market": `claude plugin marketplace add ${repo}`,
  "install-plugin": "claude plugin install yisolib@yiso-marketplace",
  "install-tarball": `tmp="$(mktemp -d)"\ncurl -fsSL ${archiveUrl} | tar -xz -C "$tmp" --strip-components=1\nmkdir -p ~/.claude/skills\ncp -R "$tmp/plugins/yisolib/skills/library" ~/.claude/skills/`,
  "prompt-draft": `${PLUGIN_COMMAND} 只试读这个目录，先列出会收录的 Memo、主归属和证据，不写数据库。`,
  "prompt-word": `${PLUGIN_COMMAND} 比对这份更新的 Word，只归档已核实的新增和修订；有候选旧稿时先让我确认。`,
  "prompt-archive": `${PLUGIN_COMMAND} 归档这批资料；新公司按主营业务归入申万 2021 二级，无法区分二级时归入一级。`,
  demo: `python assets/starter/starter.py init --db /tmp/library-demo.sqlite\npython assets/starter/starter.py import --db /tmp/library-demo.sqlite --input assets/starter/fixture.json\npython assets/starter/starter.py serve --db /tmp/library-demo.sqlite --port 8765`,
  update: "claude plugin marketplace update yiso-marketplace\nclaude plugin update yisolib",
  uninstall: "claude plugin uninstall yisolib",
};

document.querySelectorAll("[data-repo-link]").forEach((link) => {
  link.href = repoUrl;
});

document.querySelectorAll("[data-command]").forEach((node) => {
  const command = commands[node.dataset.command];
  if (command) node.textContent = command;
});

document.querySelectorAll("[data-plugin-command]").forEach((node) => {
  node.textContent = PLUGIN_COMMAND;
});

let toastTimer;
function showToast(message) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 2200);
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to the selection-based fallback.
    }
  }

  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();
  return copied;
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;

  const code = button.closest(".command")?.querySelector("code");
  if (!code) return;

  const label = button.dataset.label || button.textContent;
  button.dataset.label = label;
  const copied = await copyText(code.textContent.trim());
  button.textContent = copied ? "已复制" : "请手动复制";
  showToast(copied ? "命令已复制到剪贴板" : "复制失败，请手动选择命令");
  setTimeout(() => {
    button.textContent = label;
  }, 1600);
});
