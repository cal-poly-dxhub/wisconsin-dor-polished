import {
  Sprout,
  Caravan,
  Factory,
  Church,
  TreePine,
  Landmark,
  Scale,
  Gavel,
  Receipt,
  Percent,
  Building2,
  Store,
  Zap,
  Server,
  Calculator,
  ClipboardList,
  Map,
  Ruler,
  Users,
  ShieldCheck,
  Banknote,
  BookOpen,
  MessageSquare,
  type LucideIcon,
} from 'lucide-react';

/**
 * Map a chat session's title to a topic-relevant icon so the Recent list reads
 * at a glance. Keyword rules are ordered most-specific first; the first match
 * wins. Falls back to a neutral chat icon when nothing matches.
 *
 * Scoped to the Wisconsin property-tax assessment domain — agricultural land,
 * mobile homes, manufacturing, exemptions, appeals, TID/levy math, etc.
 */
interface TopicRule {
  icon: LucideIcon;
  /** Lowercase substrings; any match assigns this icon. */
  keywords: string[];
}

// Order matters: earlier rules take precedence on overlap (e.g. "manufactured
// home" should read as a home, not manufacturing, so home-ish terms come first
// only where the intent is clearly residential — otherwise keep them distinct).
const TOPIC_RULES: TopicRule[] = [
  { icon: Caravan, keywords: ['mobile home', 'manufactured home', 'camping trailer', 'recreational vehicle', 'rv', 'camper', 'trailer', 'park model'] },
  { icon: Sprout, keywords: ['agricultur', 'ag land', 'ag use', 'farm', 'hobby farm', 'crop', 'livestock', 'pasture', 'ginseng', 'christmas tree', 'use value'] },
  { icon: TreePine, keywords: ['forest', 'woodland', 'managed forest', 'mfl', 'undeveloped land'] },
  { icon: Church, keywords: ['church', 'religious', 'bible camp', 'worship', 'parsonage', 'nonprofit', 'charitable'] },
  { icon: Factory, keywords: ['manufactur', 'industrial', 'cabinet shop', 'sic code', 'factory'] },
  { icon: Zap, keywords: ['utility', 'light, heat', 'power company', 'solar', 'wind', 'windmill', 'megawatt', 'nameplate', 'biogas', 'energy'] },
  { icon: Server, keywords: ['data center', 'telecom', 'telephone', 'pipeline'] },
  { icon: Store, keywords: ['commercial', 'retail', 'warehouse', 'business', 'inventory', 'merchant'] },
  { icon: Gavel, keywords: ['case', 'court', 'v.', 'ruling', 'holding', 'precedent', 'wis. 2d', 'supreme court'] },
  { icon: Scale, keywords: ['appeal', 'board of review', 'bor', 'objection', 'contest', 'dispute', 'burden of proof'] },
  { icon: ShieldCheck, keywords: ['exempt', 'exemption', 'pr-230', 'taxable or exempt', 'tax exempt'] },
  { icon: Landmark, keywords: ['statute', 'wis. stat', '70.', 'admin rule', 'tax 18', 'constitution', 'chapter 70'] },
  { icon: Percent, keywords: ['levy', 'levy limit', 'mill rate', 'tax rate', 'equaliz', 'assessment ratio'] },
  { icon: Calculator, keywords: ['tid', 'tif', 'increment', 'base value', 'decrement', 'worksheet', 'calculat'] },
  { icon: Banknote, keywords: ['transfer fee', 'conveyance', 'lottery credit', 'chargeback', 'refund', 'delinquent', 'special charge'] },
  { icon: Receipt, keywords: ['property tax', 'tax bill', 'assessed value', 'assessment', 'fair market value', 'full value'] },
  { icon: Ruler, keywords: ['valuation', 'appraisal', 'income approach', 'cost approach', 'sales comparison', 'depreciation', 'grade'] },
  { icon: Map, keywords: ['parcel', 'boundary', 'plat', 'zoning', 'acreage', 'lot', 'survey', 'legal description'] },
  { icon: Building2, keywords: ['residential', 'condo', 'apartment', 'dwelling', 'improvement', 'building'] },
  { icon: Users, keywords: ['assessor', 'municipal', 'clerk', 'treasurer', 'certification', 'training', 'ownership'] },
  { icon: ClipboardList, keywords: ['form', 'filing', 'deadline', 'notice', 'report', 'schedule'] },
  { icon: BookOpen, keywords: ['wpam', 'manual', 'guide', 'faq', 'definition', 'how to'] },
];

export function iconForTitle(title: string | undefined | null): LucideIcon {
  const t = (title || '').toLowerCase();
  if (!t) return MessageSquare;
  for (const rule of TOPIC_RULES) {
    if (rule.keywords.some(k => t.includes(k))) return rule.icon;
  }
  return MessageSquare;
}
