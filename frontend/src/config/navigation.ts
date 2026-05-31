export interface AppNavItem {
  id: string
  name: string
  path: string
}

export const appNavigation: AppNavItem[] = [
  { id: 'arena', name: 'AI竞技场', path: '/arena' },
  { id: 'stock-analysis', name: '股票分析', path: '/stock-analysis' },
  { id: 'quant-research', name: '量化研究', path: '/quant-research' },
  { id: 'data-lab', name: '数据实验室', path: '/data-lab' },
  { id: 'settings', name: '设置', path: '/settings' },
]
