from setuptools import setup, find_packages

setup(name              = 'Adafruit_Video_Looper',
      version           = '1.0.19+retro',
      author            = 'Tony DiCola',
      author_email      = 'tdicola@adafruit.com',
      description       = 'Application to turn your Raspberry Pi into a dedicated looping video playback device, good for art installations, information displays, or just playing cat videos all day.',
      license           = 'GNU GPLv2',
      url               = 'https://github.com/adafruit/pi_video_looper',
      install_requires  = [
          'pyudev>=0.23.1',
          'pygame',
          'six>=1.16.0',
          'smbus',
          'starlette>=0.37',
          'uvicorn[standard]>=0.30',
      ],
      packages          = find_packages(),
      package_data      = {
          'Adafruit_Video_Looper': [
              'webui/*.html',
              'webui/*.css',
              'webui/*.jsx',
              'assets/default.mp4',
          ],
      },
      include_package_data = True)
