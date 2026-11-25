var _ = require('lodash');

//赔付率
exports.claimRatio = function (o){
  if(!Array.isArray(o) || o.length < 3){
    return 0;
  }
  var denominator = o[0]*o[1]+o[1]*o[2]+o[0]*o[2];
  if(!denominator){
    return 0;
  }
  return o[0]*o[1]*o[2]/(o[0]*o[1]+o[1]*o[2]+o[0]*o[2]);
}
//根据期望算概率
exports.probability = function (o){
  if(!Array.isArray(o) || o.length < 3){
    return [0,0,0];
  }
  var hd = o[0]*o[1];
  var da = o[1]*o[2];
  var ha = o[0]*o[2];
  var t = hd+da+ha;
  if(!t){
    return [0,0,0];
  }
  return [da/t, ha/t, hd/t];
}