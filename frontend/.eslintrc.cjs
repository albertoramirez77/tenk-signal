module.exports = {
  root: true,
  extends: ["next/core-web-vitals"],
  rules: {
    // Make missing semis / quotes a warning so prettier handles it.
    "react/no-unescaped-entities": "off",
    "@next/next/no-page-custom-font": "off",
  },
};
