<template>
  <div class="market-map-page">
    <section class="panel market-map-head">
      <div>
        <h1>市场地图</h1>
        <p>A 股全市场热力图，用于盘中观察板块轮动和个股涨跌分布。</p>
      </div>
      <a class="button ghost small" href="https://map.wenyuanw.me" target="_blank" rel="noreferrer">
        打开原站
      </a>
    </section>

    <section class="market-map-frame-shell">
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
  display: grid;
  gap: 14px;
}

.market-map-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.market-map-head h1 {
  margin: 0 0 8px;
  color: #202123;
  font-size: 24px;
  line-height: 1.1;
}

.market-map-head p {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.market-map-frame-shell {
  min-height: 620px;
  height: calc(100vh - 148px);
  overflow: hidden;
  border: 1px solid #ececf1;
  border-radius: 12px;
  background: #111318;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}

.market-map-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

@media (max-width: 980px) {
  .market-map-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .market-map-frame-shell {
    height: calc(100vh - 190px);
    min-height: 560px;
  }
}
</style>
