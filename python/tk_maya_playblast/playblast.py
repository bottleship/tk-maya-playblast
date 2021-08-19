import datetime
import os
import pprint
import re
import shutil
import sys
import traceback

from contextlib import contextmanager

import tank
from tank.platform.qt import QtCore, QtGui
from .playblast_dialog import PlayblastDialog

import pymel.core as pm
import maya.cmds as cmds
import maya.mel as mel

class PlayblastManager(object):
    __uploadToShotgun = True

    """
    Main playblast functionality
    """
    def __init__(self, app, context=None):
        """
        Construction
        """
        self._app = app
        self._context = context if context else self._app.context

    def showDialog(self):
        try:
            self._app.engine.show_dialog("Playblast %s" % self._app.version,
                                         self._app, PlayblastDialog, self._app, self)
        except:
            traceback.print_exc()

    def doPlayblast(self, **overridePlayblastParams):
        sceneName = pm.sceneName()

        self.shotPlayblastPath = self._app.execute_hook(
            "hook_setup_window",
            action="generate_path",
            data={
                "template_work": self._app.get_template("template_work"),
                "template_shot": self._app.get_template("template_shot"),
                "scene_name": sceneName,
                "params": self._app.execute_hook(
                    "hook_setup_window",
                    action="playblast_params")})

        # Get value of optional config field "temp_directory". If path is
        # invalid or not absolute, use default tempdir.
        temp_directory = os.path.normpath( self._app.get_setting("temp_directory", "default") )
        if not os.path.isabs(temp_directory):
            import tempfile
            temp_directory = tempfile.gettempdir()

        # make sure it is exists
        if not os.path.isdir(temp_directory):
            os.mkdir(temp_directory)
        # use the basename of generated names
        self.localPlayblastPath = os.path.join(
            temp_directory,
            os.path.basename(self.shotPlayblastPath))

        # run actual playblast routine
        self.__createPlayblast(**overridePlayblastParams)
        self._app.log_info("Playblast for %s succesful" % sceneName)

    def __createPlayblast(self, **overridePlayblastParams):
        localPlayblastPath = self.localPlayblastPath

        # setting playback range
        originalPlaybackRange = ( pm.playbackOptions( query=True, minTime=True ),
                                  pm.playbackOptions( query=True, maxTime=True ))
        startTime = pm.playbackOptions( query=True, animationStartTime=True )
        endTime = pm.playbackOptions( query=True, animationEndTime=True )
        pm.playbackOptions( edit=True, minTime=startTime, maxTime=endTime )

        # TODO This is a bit iffy
        splitPlayblastPath = os.path.splitext(localPlayblastPath)
        if splitPlayblastPath[-1] in [".mov", ".mp4", ".avi"]:
            playblastParamPath = splitPlayblastPath[0]
        else:
            # for sequences also remove sequence placeholder
            playblastParamPath = os.path.splitext(splitPlayblastPath[0])[0]

        # get playblast parameters from hook
        playblastParams = self._app.execute_hook(
            "hook_setup_window",
            action="playblast_params",
            data=playblastParamPath)

        # get window and editor parameters from hook
        createWindow = self._app.execute_hook("hook_setup_window", action='create_window')

        # with the created window, do a playblast
        with createWindow():
            playblastParams.update(overridePlayblastParams)
            playblastSuccessful = False
            while not playblastSuccessful:
                try:
                    # set required visibleHUDs from hook
                    visibleHUDs = self._app.execute_hook("hook_setup_window", action="hud_set")
                    self._app.log_info("Playblast params: {}".format(playblastParams))
                    resultPlayblastPath = pm.playblast(**playblastParams)
                    playblastSuccessful = True
                except RuntimeError as e:
                    result = QtGui.QMessageBox.critical(
                        None, u"Playblast Error",
                        "{}\n\n... or just close your QuickTime player, and Retry.".format(e),
                        QtGui.QMessageBox.Retry | QtGui.QMessageBox.Abort)
                    if result == QtGui.QMessageBox.Abort:
                        self._app.log_error("Playblast aborted")
                        return
                finally:
                    # restore HUD state
                    self._app.execute_hook("hook_setup_window", action="hud_unset", data=visibleHUDs)
                    # restore playback range
                    originalMinTime, originalMaxTime = originalPlaybackRange
                    pm.playbackOptions( edit=True, minTime=originalMinTime, maxTime=originalMaxTime )

        # do post playblast process, copy files and other necessary stuff
        result = self._app.execute_hook(
            "hook_post_playblast",
            action="copy_file",
            data={
                "local_path": localPlayblastPath,
                "shot_path": self.shotPlayblastPath,
                "startTime": startTime,
                "endTime": endTime
            })

        if result:
            self._app.log_info("Playblast local file created: %s" % localPlayblastPath)

        # register new Version entity in shotgun or update existing version, minimize shotgun data
        playblast_path = self.shotPlayblastPath
        project = self._app.context.project
        entity = self._app.context.entity
        task = self._app.context.task

        data = {'project': project,
                'code': os.path.basename(playblast_path),
                'description': 'automatic generated by playblast app',
                'entity': entity,
                'sg_task': task,
                'sg_scenefile': pm.sceneName(),
                'sg_first_frame': int(startTime),
                'sg_last_frame': int(endTime),
                }

        is_movie = playblastParams["format"] in ["movie", "qt", "avi"]
        # set the correct field depending on whether the render is a movie
        if is_movie:
            data["sg_path_to_movie"] = playblast_path
        else:
            data["sg_path_to_frames"] = playblast_path

        self._app.log_debug("Version-creation hook data:\n" + pprint.pformat(data))
        result = self._app.execute_hook("hook_post_playblast", action="create_version", data=data)
        self._app.log_debug("Version-creation hook result:\n" + pprint.pformat(result))

        # upload QT file if creation or update process run succesfully
        if result and self.__uploadToShotgun and is_movie:
            self._app.log_info("Uploading playblast to Shotgun")
            result = self._app.execute_hook("hook_post_playblast", action="upload_movie",
                                            data=dict(path=data["sg_path_to_movie"],
                                                      project=project,
                                                      version_id=result["id"]))

        self._app.log_info("Playblast finished")

    def setUploadToShotgun(self, value):
        self._app.log_debug("Upload to Shotgun set to %s" % value)
        self.__uploadToShotgun = value
