'use strict';
// Shared by circle overlays and their accuracy rings; no network or map state.
const mapGeometry = {
  covers(points, lng, lat) {
    if (points.length < 3) return false;
    let inside = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
      const [x, y] = points[i], [u, v] = points[j];
      const cross = (lng-x)*(v-y) - (lat-y)*(u-x);
      if (Math.abs(cross) < 1e-12 && lng >= Math.min(x,u)-1e-12 && lng <= Math.max(x,u)+1e-12
          && lat >= Math.min(y,v)-1e-12 && lat <= Math.max(y,v)+1e-12) return true;
      if ((y > lat) !== (v > lat) && lng < (u-x)*(lat-y)/(v-y)+x) inside = !inside;
    }
    return inside;
  },
  circle(lng, lat, radius, segments = 96) {
    const radians = Math.PI / 180;
    const latitude = lat * radians, longitude = lng * radians;
    const angular = radius / 6371008.8;
    const points = [];
    for (let i = 0; i < segments; i++) {
      const bearing = i * 2 * Math.PI / segments;
      const y = Math.asin(Math.sin(latitude) * Math.cos(angular) + Math.cos(latitude) * Math.sin(angular) * Math.cos(bearing));
      const x = longitude + Math.atan2(Math.sin(bearing) * Math.sin(angular) * Math.cos(latitude), Math.cos(angular) - Math.sin(latitude) * Math.sin(y));
      points.push([((x / radians + 540) % 360) - 180, y / radians]);
    }
    return this.polygon(points);
  },
  polygon(points) {
    // Terra Draw validates decimal precision. MapLibre unproject() can return
    // 13+ decimal places; nine keeps sub-millimetre precision without rejection.
    const ring = points.map(point => point.map(value => Number(value.toFixed(9))));
    if (ring.length) ring.push([...ring[0]]);
    return {type: 'Feature', properties: {}, geometry: {type: 'Polygon', coordinates: [ring]}};
  }
};
if (typeof module !== 'undefined') module.exports = mapGeometry;
