<template>
  <div class="market-map-page">
    <section class="market-map-frame-shell">
      <a class="market-map-open" href="https://map.wenyuanw.me" target="_blank" rel="noreferrer">
        原站
      </a>
      <iframe
        class="market-map-frame"
        src="https://map.wenyuanw.me"
        title="A 股市场热力图"
        loading="eager"
        referrerpolicy="no-referrer"
        allow="clipboard-write; fullscreen"
        allowfullscreen
      ></iframe>
    </section>
  </div>
</template>

<style scoped>
.market-map-page {
  min-width: 0;
}

.market-map-frame-shell {
  position: relative;
  min-height: 720px;
  height: calc(100vh - 44px);
  overflow: hidden;
  border: 1px solid #ececf1;
  border-radius: 12px;
  background: #111318;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}

.market-map-open {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 54px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 10px;
  background: rgba(17, 19, 24, 0.72);
  color: #ffffff;
  font-size: 12px;
  font-weight: 650;
  text-decoration: none;
  backdrop-filter: blur(8px);
}

.market-map-open:hover {
  background: rgba(17, 19, 24, 0.88);
}

.market-map-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

@media (max-width: 980px) {
  .market-map-frame-shell {
    height: calc(100vh - 44px);
    min-height: 620px;
  }
}
</style>
