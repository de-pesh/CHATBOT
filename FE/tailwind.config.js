/** @type {import('tailwindcss').Config} */
export default {
  prefix: "tw-",
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#2e2e38', // custom color name
        // primaryText: '#ffffff', // custom text color name
        // tileImageBackGround: '#ffffff', // custom background color for time image
        // buttonFilled: '#2E2E38', // custom color for filled buttons
        // buttonLight: '#C3C3CB', // custom color for light buttons
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'], // using Inter as the default sans-serif font
      },
    },
  },
  plugins: [],
}

