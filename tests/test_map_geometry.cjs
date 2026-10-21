const assert = require('node:assert/strict');
const geometry = require('../static/map-geometry.js');

const source = [[-74,40],[-73.9,40],[-73.9,40.1]];
const feature = geometry.polygon(source);
assert.equal(source.length, 3, 'Closing a polygon must not mutate the saved draft');
assert.deepEqual(feature.geometry.coordinates[0].at(-1), source[0]);
feature.geometry.coordinates[0][0][0] = 1;
assert.equal(source[0][0], -74, 'Geometry must not alias saved coordinates');
const precise = geometry.polygon([[-73.870011612345,40.734596512345],[-73.87,40.735],[-73.869,40.734]]);
assert.deepEqual(precise.geometry.coordinates[0][0], [-73.870011612,40.734596512], 'Map click precision must remain accepted by the drawing editor');

const circle = geometry.circle(-73.98, 40.75, 8046.72);
const ring = circle.geometry.coordinates[0];
assert.equal(ring.length, 97);
assert.deepEqual(ring[0], ring.at(-1));
for (const [lng, lat] of ring) {
  const r = Math.PI / 180;
  const a = Math.sin((lat-40.75)*r/2)**2 + Math.cos(40.75*r)*Math.cos(lat*r)*Math.sin((lng+73.98)*r/2)**2;
  const meters = 2*6371008.8*Math.asin(Math.sqrt(a));
  assert.ok(Math.abs(meters-8046.72)<.001, 'Circle must use radius = diameter / 2, with longitude first');
}
console.log('Map polygon isolation, ring closure and circle radius passed');

const square=[[-74,40.7],[-73.9,40.7],[-73.9,40.8],[-74,40.8]];
assert.equal(geometry.covers(square,-73.98,40.75),true);
assert.equal(geometry.covers(square,-74,40.75),true,'Boundary belongs to shape');
assert.equal(geometry.covers(square,-74,40.7),true,'Vertex belongs to shape');
assert.equal(geometry.covers(square,-74.01,40.75),false);
const concave=[[0,0],[3,0],[3,3],[2,3],[2,1],[1,1],[1,3],[0,3]];
assert.equal(geometry.covers(concave,1.5,2),false,'Notch is outside, even within bounds');
assert.equal(geometry.covers(concave,.5,2),true);
