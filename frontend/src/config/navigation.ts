export interface AppNavItem {
  id: string
  name: string
  path: string
  icon: string
}

export const appNavigation: AppNavItem[] = [
  { id: 'arena', name: 'AI竞技场', path: '/arena', icon: 'emoji_events' },
  { id: 'market-map', name: '市场地图', path: '/market-map', icon: 'map' },
  { id: 'stock-analysis', name: '股票分析', path: '/stock-analysis', icon: 'query_stats' },
  { id: 'quant-research', name: '量化研究', path: '/quant-research', icon: 'science' },
  { id: 'data-lab', name: '数据实验室', path: '/data-lab', icon: 'database' },
  { id: 'settings', name: '设置', path: '/settings', icon: 'settings' },
]
