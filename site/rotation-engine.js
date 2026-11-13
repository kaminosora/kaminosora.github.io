/* Sora's content-driven rotation. No DOM, network, storage, or input mutation. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SoraRotation = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const weight = value => value == null ? 1 : finite(value) && value > 0 ? value : 0;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const validId = value => typeof value === 'string' && value.length > 0;
  const list = value => Array.isArray(value) ? value : [];
  const multiply = (...values) => values.reduce((result, value) => Math.min(Number.MAX_VALUE, result * value), 1);

  function indexById(items) {
    const result = new Map();
    for (const item of list(items)) {
      if (item && validId(item.id) && !result.has(item.id)) result.set(item.id, item);
    }
    return result;
  }

  function normalized(items, readWeight) {
    const values = items.map(item => readWeight(item));
    const largest = Math.max(0, ...values);
    if (!largest) return values.map(() => 0);
    const scaled = values.map(value => value / largest);
    const sum = scaled.reduce((total, value) => total + value, 0);
    return scaled.map(value => value / sum);
  }

  function createLibrary(data, settings = {}) {
    if (!data || typeof data !== 'object') data = {};
    const recentWindow = finite(settings.recentWindow) ? Math.floor(clamp(settings.recentWindow, 0, 128)) : 6;
    const noveltyBoost = finite(settings.noveltyBoost) ? clamp(settings.noveltyBoost, 0, 5) : 0.6;
    const recentPenalty = finite(settings.recentPenalty) ? clamp(settings.recentPenalty, 0, 5) : 0.35;
    const counts = new Map(), ipCounts = new Map(), groupCounts = new Map();
    const recent = [];
    let total = 0, lastId = null;

    function identify(asset, ips, characters) {
      if (!asset || !validId(asset.id)) return null;
      const ip = ips.get(asset.ipId);
      if (!ip) return null;
      const characterIds = [...new Set(list(asset.characterIds))].sort();
      if (characterIds.some(id => !validId(id) || !characters.has(id) || characters.get(id).ipId !== ip.id)) return null;
      const members = characterIds.map(id => characters.get(id));
      const characterGroup = characterIds.length ? 'characters:' + JSON.stringify(characterIds) : 'scene';
      return { asset, ip, members, characterGroup, groupKey: JSON.stringify([ip.id, characterGroup]) };
    }

    function novelty(count) { return 1 + noveltyBoost / (1 + (count || 0)); }
    function recency(field, id) {
      return 1 / (1 + recentPenalty * recent.filter(entry => entry[field] === id).length);
    }
    function layout(asset, viewport) {
      const [width, height] = list(asset.dimensions);
      if (!finite(width) || !finite(height) || width <= 0 || height <= 0) return 1;
      const ratio = width / height;
      if (viewport === 'mobile') return ratio <= 1 ? 1.15 : ratio <= 1.4 ? 1 : 0.85;
      if (viewport === 'desktop') return ratio >= 1.2 ? 1.1 : 1;
      return 1;
    }

    function distribution(query = {}) {
      const ips = indexById(data.ips), characters = indexById(data.characters);
      const use = typeof query.use === 'string' ? query.use : 'hero';
      const viewport = query.viewport || 'desktop';
      const candidates = [];
      for (const asset of indexById(data.assets).values()) {
        const item = identify(asset, ips, characters);
        if (!item || (query.ipId != null && item.ip.id !== query.ipId)) continue;
        const { ip, members } = item;
        if (asset.enabled === false || ip.enabled === false || (use === 'hero' && ip.rotation?.enabled === false)) continue;
        const ipWeight = use === 'hero' ? weight(ip.rotation?.weight) : weight(ip.weight);
        if (!weight(asset.weight) || !ipWeight || !list(asset.uses).includes(use)) continue;
        if (members.some(member => member.enabled === false || !weight(member.weight))) continue;
        candidates.push(item);
      }
      if (!candidates.length) return [];
      const ipGroups = new Map();
      const result = candidates.map(({ asset, ip, characterGroup }) => ({ id: asset.id, ipId: ip.id, characterGroup, probability: 0 }));
      const resultById = new Map(result.map(item => [item.id, item]));

      // The immediate repeat is excluded globally, then the eligible hierarchy
      // is renormalized. A sole available picture still remains playable.
      for (const item of candidates) {
        if (candidates.length > 1 && item.asset.id === lastId) continue;
        if (!ipGroups.has(item.ip.id)) ipGroups.set(item.ip.id, { ip: item.ip, groups: new Map() });
        const ipGroup = ipGroups.get(item.ip.id);
        if (!ipGroup.groups.has(item.characterGroup)) {
          // Group images use the mean character weight, not the sum; adding a
          // second character does not grant the group two lottery tickets.
          const memberWeights = item.members.map(member => weight(member.weight));
          const memberMax = Math.max(1, ...memberWeights);
          const groupWeight = memberWeights.length ? memberWeights.reduce((sum, value) => sum + value / memberMax, 0) / memberWeights.length * memberMax : 1;
          ipGroup.groups.set(item.characterGroup, { key: item.groupKey, weight: groupWeight, items: [] });
        }
        ipGroup.groups.get(item.characterGroup).items.push(item);
      }

      const ipEntries = [...ipGroups.values()];
      const ipProbabilities = normalized(ipEntries, entry => multiply(use === 'hero' ? weight(entry.ip.rotation?.weight) : weight(entry.ip.weight), novelty(ipCounts.get(entry.ip.id)), recency('ipId', entry.ip.id)));
      ipEntries.forEach((ipEntry, ipIndex) => {
        const groups = [...ipEntry.groups.values()];
        const groupProbabilities = normalized(groups, group => multiply(group.weight, novelty(groupCounts.get(group.key)), recency('groupKey', group.key)));
        groups.forEach((group, groupIndex) => {
          const pictureProbabilities = normalized(group.items, ({ asset }) => multiply(
            weight(asset.weight), finite(asset.quality) ? clamp(asset.quality, 0.55, 1) : 1,
            layout(asset, viewport), novelty(counts.get(asset.id)), recency('id', asset.id)
          ));
          group.items.forEach(({ asset }, pictureIndex) => {
            resultById.get(asset.id).probability = ipProbabilities[ipIndex] * groupProbabilities[groupIndex] * pictureProbabilities[pictureIndex];
          });
        });
      });
      return result;
    }

    function record(assetOrId) {
      const id = typeof assetOrId === 'string' ? assetOrId : assetOrId?.id;
      const asset = indexById(data.assets).get(id);
      const item = identify(asset, indexById(data.ips), indexById(data.characters));
      if (!item) return false;
      total += 1;
      lastId = id;
      counts.set(id, (counts.get(id) || 0) + 1);
      ipCounts.set(item.ip.id, (ipCounts.get(item.ip.id) || 0) + 1);
      groupCounts.set(item.groupKey, (groupCounts.get(item.groupKey) || 0) + 1);
      if (recentWindow > 0) {
        recent.push({ id, ipId: item.ip.id, groupKey: item.groupKey });
        if (recent.length > recentWindow) recent.shift();
      }
      return true;
    }

    function next(query = {}, rng = Math.random) {
      const choices = distribution(query).filter(item => item.probability > 0);
      if (!choices.length) return null;
      const raw = rng();
      const random = Number.isNaN(raw) || typeof raw !== 'number' ? 0 : clamp(raw, 0, 1 - Number.EPSILON);
      let cumulative = 0;
      let selected = choices[choices.length - 1];
      for (const choice of choices) {
        cumulative += choice.probability;
        if (random < cumulative) { selected = choice; break; }
      }
      const asset = indexById(data.assets).get(selected.id);
      record(asset);
      return asset;
    }

    function getHistory() {
      return {
        total, lastId, counts: Object.fromEntries(counts), ipCounts: Object.fromEntries(ipCounts),
        groupCounts: Object.fromEntries(groupCounts), recent: recent.map(entry => ({ ...entry }))
      };
    }
    return { next, record, distribution, getHistory };
  }
  return { createLibrary };
});
