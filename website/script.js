(() => {
  const REPO = "Duang777/forgepilot-agent";
  const RELEASES_URL = `https://github.com/${REPO}/releases`;

  function pickAsset(assets, platform) {
    const byPlatform = {
      windows: [/windows/i, /win/i, /x64/i, /\.exe$/i, /\.msi$/i],
      macos: [/macos/i, /darwin/i, /aarch64/i, /arm64/i, /\.dmg$/i, /\.pkg$/i],
      linux: [/linux/i, /appimage/i, /x86_64/i, /\.AppImage$/i, /\.deb$/i, /\.rpm$/i],
    };

    const rules = byPlatform[platform] || [];
    if (!Array.isArray(assets) || assets.length === 0) {
      return null;
    }

    for (const asset of assets) {
      const name = String(asset.name || "");
      if (rules.some((rule) => rule.test(name))) {
        return asset;
      }
    }

    return null;
  }

  function updateReleaseUi(payload) {
    const links = document.querySelectorAll(".download-link");
    const tip = document.getElementById("download-tip");
    const version = document.getElementById("latest-version");
    const status = document.getElementById("release-status");
    const targets = document.getElementById("build-targets");

    if (!payload) {
      if (tip) {
        tip.textContent = "未获取到 latest release，已回退到 Releases 页面。";
      }
      if (version) {
        version.textContent = "no release";
      }
      if (status) {
        status.textContent = "pending";
      }
      if (targets) {
        targets.textContent = "Pending / Pending / Pending";
      }

      links.forEach((link) => {
        link.href = RELEASES_URL;
        link.textContent = "打开 Releases";
      });

      return;
    }

    const assets = Array.isArray(payload.assets) ? payload.assets : [];
    if (version) {
      version.textContent = payload.tag_name || "latest";
    }
    if (status) {
      status.textContent = "published";
    }
    if (tip) {
      tip.textContent = `已同步 ${payload.tag_name || "latest"}，共 ${assets.length} 个资产文件。`;
    }

    let resolvedCount = 0;

    links.forEach((link) => {
      const platform = link.getAttribute("data-platform");
      const asset = pickAsset(assets, platform);
      const card = link.closest(".download-card");
      const copyBtn = card ? card.querySelector(".copy-btn") : null;

      if (asset) {
        link.href = asset.browser_download_url || RELEASES_URL;
        link.textContent = `下载 ${asset.name}`;
        resolvedCount += 1;

        if (copyBtn) {
          const digest = String(asset.digest || "").trim();
          copyBtn.setAttribute("data-copy", digest || `no-digest:${asset.name}`);
        }
      } else {
        link.href = RELEASES_URL;
        link.textContent = "打开 Releases";

        if (copyBtn) {
          copyBtn.setAttribute("data-copy", "该资产未提供校验码");
        }
      }
    });

    if (targets) {
      targets.textContent = `${resolvedCount}/3 resolved`;
    }
  }

  async function loadLatestRelease() {
    try {
      const response = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
        headers: {
          Accept: "application/vnd.github+json",
        },
      });

      if (!response.ok) {
        updateReleaseUi(null);
        return;
      }

      const payload = await response.json();
      updateReleaseUi(payload);
    } catch {
      updateReleaseUi(null);
    }
  }

  function bindCopyButtons() {
    const copyButtons = document.querySelectorAll(".copy-btn");

    copyButtons.forEach((button) => {
      button.addEventListener("click", async () => {
        const text = button.getAttribute("data-copy") || "";
        if (!text) {
          return;
        }

        try {
          await navigator.clipboard.writeText(text);
          const original = button.textContent;
          button.textContent = "已复制";
          setTimeout(() => {
            button.textContent = original;
          }, 1200);
        } catch {
          button.textContent = "复制失败";
          setTimeout(() => {
            button.textContent = "复制校验码";
          }, 1000);
        }
      });
    });
  }

  function bindReveal() {
    const items = document.querySelectorAll(".reveal");
    if (!items.length) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.2 }
    );

    items.forEach((item) => observer.observe(item));
  }

  function setCurrentYear() {
    const year = document.getElementById("current-year");
    if (year) {
      year.textContent = String(new Date().getFullYear());
    }
  }

  bindCopyButtons();
  bindReveal();
  setCurrentYear();
  loadLatestRelease();
})();
